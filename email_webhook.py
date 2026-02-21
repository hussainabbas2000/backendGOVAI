"""
Email Webhook Module
Receives and parses incoming supplier email replies via SendGrid Inbound Parse
"""

from flask import Blueprint, request, jsonify
from openai import OpenAI
import os
import json
import re
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Create Blueprint
webhook_bp = Blueprint('webhook', __name__, url_prefix='/webhook')

# Initialize OpenAI client for email parsing
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Database reference (set by init function)
db = None
Supplier = None
Message = None
NegotiationSession = None


def init_webhook_db(database, supplier_model, message_model, session_model):
    """Initialize database and models from main app"""
    global db, Supplier, Message, NegotiationSession
    db = database
    Supplier = supplier_model
    Message = message_model
    NegotiationSession = session_model


def parse_email_for_quote(email_content: str, from_email: str, subject: str) -> dict:
    """
    Use AI to parse email content and extract quote information
    
    Args:
        email_content: Body of the email
        from_email: Sender's email address
        subject: Email subject line
    
    Returns:
        dict with parsed information
    """
    
    prompt = f"""
    Parse this vendor email response and extract the following information:
    
    Email Subject: {subject}
    From: {from_email}
    
    Email Content:
    {email_content[:3000]}  # Limit content length
    
    Extract and return as JSON:
    1. "is_quote_response": boolean - Is this a response to a quote request?
    2. "total_price": number or null - Total quoted price mentioned
    3. "unit_price": number or null - Per-unit price if mentioned
    4. "delivery_timeline": string or null - Delivery timeline mentioned
    5. "payment_terms": string or null - Payment terms mentioned
    6. "key_points": list of strings - Key points from the response
    7. "is_negotiation": boolean - Is the vendor negotiating/counter-offering?
    8. "sentiment": string - "positive", "neutral", or "negative"
    9. "requires_followup": boolean - Does this need a follow-up response?
    10. "summary": string - Brief summary of the email (max 100 words)
    
    Return ONLY valid JSON, no other text.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert at parsing business emails and extracting pricing information."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        
        content = response.choices[0].message.content
        
        # Clean up markdown code blocks if present
        if content.startswith('```'):
            content = content.split('```')[1]
            if content.startswith('json'):
                content = content[4:]
        
        return json.loads(content.strip())
        
    except Exception as e:
        return {
            'is_quote_response': True,
            'total_price': extract_price_from_text(email_content),
            'unit_price': None,
            'delivery_timeline': None,
            'payment_terms': None,
            'key_points': [],
            'is_negotiation': False,
            'sentiment': 'neutral',
            'requires_followup': True,
            'summary': f'Email received from {from_email}. Manual review required.',
            'parse_error': str(e)
        }


def extract_price_from_text(text: str) -> float:
    """Extract total price from text using regex patterns"""
    
    patterns = [
        r'total:?\s*\$?([\d,]+\.?\d*)',
        r'total\s*(?:cost|price|amount):?\s*\$?([\d,]+\.?\d*)',
        r'grand\s*total:?\s*\$?([\d,]+\.?\d*)',
        r'\$?([\d,]+\.?\d*)\s*total',
        r'quoted\s*(?:price|amount):?\s*\$?([\d,]+\.?\d*)',
        r'price:?\s*\$?([\d,]+\.?\d*)',
        r'\$\s*([\d,]+\.?\d*)'
    ]
    
    text_lower = text.lower()
    
    for pattern in patterns:
        matches = re.findall(pattern, text_lower)
        if matches:
            price_str = matches[-1].replace(',', '')
            try:
                price = float(price_str)
                if price > 100:  # Assume prices are > $100
                    return price
            except:
                continue
    
    return None


def find_supplier_by_email(email_address: str):
    """Find the most recent supplier with this email that was actually sent an RFQ."""
    if not Supplier:
        return None
    
    # Match the most recent supplier that has email_sent=True (actively awaiting reply)
    supplier = (
        Supplier.query
        .filter_by(email=email_address, email_sent=True)
        .order_by(Supplier.id.desc())
        .first()
    )
    
    if supplier:
        return supplier
    
    # Fallback: most recent supplier with this email (any state)
    supplier = (
        Supplier.query
        .filter_by(email=email_address)
        .order_by(Supplier.id.desc())
        .first()
    )
    
    if not supplier:
        domain = email_address.split('@')[1] if '@' in email_address else None
        if domain:
            supplier = (
                Supplier.query
                .filter(Supplier.email.contains(domain))
                .order_by(Supplier.id.desc())
                .first()
            )
    
    return supplier


def create_message_from_email(supplier_id: int, parsed_data: dict, raw_content: str) -> dict:
    """Create a message record from parsed email"""
    
    if not Message or not db:
        return {'error': 'Database not initialized'}
    
    try:
        # Duplicate detection: skip if an identical supplier message was created recently
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        duplicate = Message.query.filter(
            Message.supplier_id == supplier_id,
            Message.sender == 'supplier',
            Message.content == raw_content,
            Message.created_at >= cutoff
        ).first()
        if duplicate:
            print(f"[EmailWebhook] Skipping duplicate message for supplier {supplier_id}")
            return {'message_id': duplicate.id, 'supplier_id': supplier_id, 'duplicate': True}
        
        msg = Message(
            supplier_id=supplier_id,
            sender='supplier',
            content=raw_content,
            price_mentioned=parsed_data.get('total_price')
        )
        db.session.add(msg)
        
        # Update supplier status
        supplier = Supplier.query.get(supplier_id)
        if supplier:
            if supplier.status == 'pending':
                supplier.status = 'negotiating'
            if parsed_data.get('total_price') and not supplier.initial_price:
                supplier.initial_price = parsed_data.get('total_price')
        
        db.session.commit()
        
        return {
            'message_id': msg.id,
            'supplier_id': supplier_id,
            'price_mentioned': parsed_data.get('total_price'),
            'created_at': msg.created_at.isoformat()
        }
        
    except Exception as e:
        db.session.rollback()
        return {'error': str(e)}


# API Routes
@webhook_bp.route('/email/inbound', methods=['POST'])
def receive_inbound_email():
    """
    Webhook endpoint for SendGrid Inbound Parse
    Receives and processes incoming emails from suppliers
    """
    
    try:
        # SendGrid sends form data
        from_email = request.form.get('from', '')
        to_email = request.form.get('to', '')
        subject = request.form.get('subject', '')
        text_body = request.form.get('text', '')
        html_body = request.form.get('html', '')
        
        # Extract email address from "Name <email@domain.com>" format
        if '<' in from_email:
            from_email = from_email.split('<')[1].split('>')[0]
        
        # Use text body preferably, fall back to HTML
        email_content = text_body or html_body
        
        if not email_content:
            return jsonify({'error': 'No email content received'}), 400
        
        # Find the supplier
        supplier = find_supplier_by_email(from_email)
        
        if not supplier:
            # Log unknown sender but don't fail
            print(f"Email received from unknown sender: {from_email}")
            return jsonify({
                'status': 'received',
                'matched_supplier': False,
                'from': from_email
            })
        
        # Parse the email content
        parsed_data = parse_email_for_quote(email_content, from_email, subject)
        
        # Create message record
        message_result = create_message_from_email(
            supplier.id, 
            parsed_data, 
            email_content
        )
        
        # Create notification if this is a quote response
        if parsed_data.get('is_quote_response'):
            try:
                from notifications import create_vendor_response_notification
                
                session = NegotiationSession.query.get(supplier.session_id)
                
                create_vendor_response_notification(
                    vendor_name=supplier.company_name,
                    opportunity_title=session.opportunity_title if session else 'Government Contract',
                    session_id=supplier.session_id,
                    supplier_id=supplier.id,
                    round_number=supplier.negotiation_round,
                    opportunity_id=session.opportunity_id if session else None
                )
            except Exception as e:
                print(f"Failed to create notification: {e}")
        
        return jsonify({
            'status': 'processed',
            'supplier_id': supplier.id,
            'supplier_name': supplier.company_name,
            'parsed_data': parsed_data,
            'message': message_result
        })
        
    except Exception as e:
        print(f"Error processing inbound email: {e}")
        return jsonify({'error': str(e)}), 500


@webhook_bp.route('/email/test', methods=['POST'])
def test_email_webhook():
    """Test endpoint to simulate receiving an email"""
    
    data = request.json
    from_email = data.get('from_email')
    subject = data.get('subject', 'Re: Request for Quotation')
    content = data.get('content', '')
    
    if not from_email or not content:
        return jsonify({'error': 'from_email and content are required'}), 400
    
    # Find supplier
    supplier = find_supplier_by_email(from_email)
    
    if not supplier:
        return jsonify({
            'status': 'no_match',
            'message': f'No supplier found with email: {from_email}'
        })
    
    # Parse email
    parsed_data = parse_email_for_quote(content, from_email, subject)
    
    # Create message
    message_result = create_message_from_email(supplier.id, parsed_data, content)
    
    return jsonify({
        'status': 'processed',
        'supplier': {
            'id': supplier.id,
            'company_name': supplier.company_name,
            'email': supplier.email
        },
        'parsed_data': parsed_data,
        'message': message_result
    })


@webhook_bp.route('/email/parse', methods=['POST'])
def parse_email_content():
    """Parse email content without saving (for testing/preview)"""
    
    data = request.json
    email_content = data.get('content', '')
    from_email = data.get('from_email', 'unknown@email.com')
    subject = data.get('subject', 'Quote Response')
    
    if not email_content:
        return jsonify({'error': 'content is required'}), 400
    
    parsed_data = parse_email_for_quote(email_content, from_email, subject)
    
    return jsonify(parsed_data)


@webhook_bp.route('/status', methods=['GET'])
def webhook_status():
    """Check webhook endpoint status"""
    return jsonify({
        'status': 'active',
        'timestamp': datetime.utcnow().isoformat(),
        'database_connected': db is not None
    })

