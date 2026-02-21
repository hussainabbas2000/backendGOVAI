from flask import Blueprint, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from openai import OpenAI
import os
import json
import re
from dotenv import load_dotenv
from email_service import send_rfq_email, send_negotiation_email, send_notification_email

load_dotenv()

# Create Blueprint
quote_bp = Blueprint('quote', __name__, url_prefix='/api')

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# We'll use the app's db instance
db = None

def init_db(database):
    """Initialize database from main app"""
    global db, NegotiationSession, Supplier, Message
    db = database
    
    # Database Models
    class NegotiationSession(db.Model):
        __tablename__ = 'negotiation_sessions'
        id = db.Column(db.Integer, primary_key=True)
        opportunity_id = db.Column(db.String(200), nullable=False)
        opportunity_title = db.Column(db.Text)
        opportunity_data = db.Column(db.Text)  # JSON string
        target_price = db.Column(db.Float, nullable=False)
        extracted_requirements = db.Column(db.Text)  # JSON string
        status = db.Column(db.String(50), default='active')
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        
        suppliers = db.relationship('Supplier', backref='session', lazy=True)

    class Supplier(db.Model):
        __tablename__ = 'suppliers'
        
        id = db.Column(db.Integer, primary_key=True)
        session_id = db.Column(db.Integer, db.ForeignKey('negotiation_sessions.id'), nullable=False)
        company_name = db.Column(db.String(200))
        email = db.Column(db.String(200))  # Supplier email address
        notes = db.Column(db.Text)  # Optional notes for manual vendors
        is_manual = db.Column(db.Boolean, default=False)  # Flag to identify manually added vendors
        industry = db.Column(db.String(100))
        initial_price = db.Column(db.Float)
        final_price = db.Column(db.Float)
        status = db.Column(db.String(50), default='pending')
        negotiation_round = db.Column(db.Integer, default=0)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        # Email tracking fields
        email_sent = db.Column(db.Boolean, default=False)
        email_sent_at = db.Column(db.DateTime, nullable=True)
        last_email_message_id = db.Column(db.String(100), nullable=True)
        
        messages = db.relationship('Message', backref='supplier', lazy=True, order_by='Message.created_at')

    class Message(db.Model):
        __tablename__ = 'messages'
        
        id = db.Column(db.Integer, primary_key=True)
        supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=False)
        sender = db.Column(db.String(50))  # 'buyer' or 'supplier'
        content = db.Column(db.Text)
        price_mentioned = db.Column(db.Float)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Make models available globally in this module
    globals()['NegotiationSession'] = NegotiationSession
    globals()['Supplier'] = Supplier
    globals()['Message'] = Message

# AI Functions
def extract_requirements_from_opportunity(opportunity_data):
    """Extract key requirements from SAM.gov opportunity using AI"""
    
    # Prepare the opportunity details
    details = opportunity_data

    
    prompt = f"""
    Analyze this government contract opportunity and extract the key requirements:
    
    {details}
    
    Extract and return a JSON object with:
    1. "product_service": Main product or service needed (be specific)
    2. "quantity": Estimated quantity if mentioned, otherwise "As specified in RFP"
    3. "delivery_location": Where the work will be performed or delivered
    4. "key_requirements": List of 3-5 most important requirements
    5. "certifications_needed": Any certifications or clearances mentioned
    6. "timeline": Delivery timeline or project duration
    7. "industry_category": Best matching industry (tech, construction, services, supplies, etc.)
    8. "suggested_suppliers": An array of exactly 5 supplier objects, each with:
       - "name": Realistic company name for this type of government contract work
       - "email": A realistic business email address (e.g., sales@companyname.com, quotes@company.com, rfq@company.com)
    
    Example format for suggested_suppliers:
    [
        {{"name": "Federal Supply Solutions", "email": "quotes@federalsupply.com"}},
        {{"name": "Government Services Inc", "email": "rfq@govservices.com"}}
    ]
    
    Return ONLY the JSON object, no other text.
    """
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an expert at analyzing government contracts and extracting requirements."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )
    
    try:
        content = response.choices[0].message.content
        # Clean up markdown code blocks if present
        if content.startswith('```'):
            content = content.split('```')[1]
            if content.startswith('json'):
                content = content[4:]
        return json.loads(content.strip())
    except:
        # Fallback if JSON parsing fails
        return {
            "product_service": opportunity_data.get('title', 'Government Contract Services'),
            "quantity": "As specified in RFP",
            "delivery_location": "As specified",
            "key_requirements": ["Meet all RFP requirements", "Timely delivery", "Quality assurance"],
            "certifications_needed": ["As required"],
            "timeline": "As per contract",
            "industry_category": "services",
            "suggested_suppliers": [
                {"name": "Federal Contractors Inc", "email": "quotes@federalcontractors.com"},
                {"name": "Government Solutions LLC", "email": "rfq@govsolutions.com"},
                {"name": "Contract Services Corp", "email": "sales@contractservices.com"},
                {"name": "Public Sector Partners", "email": "bids@publicsector.com"},
                {"name": "Federal Supply Company", "email": "quotes@federalsupply.com"}
            ]
        }

def generate_initial_request(opportunity,requirements, company_name, additional_requirements=""):
    """Generate initial quote request based on extracted requirements"""
    
    prompt = f"""
    Write a professional quote request email for a government contract opportunity:
    
    Product/Service: {requirements['product_service']}
    Quantity: {requirements['quantity']}
    Delivery Location: {requirements['delivery_location']}
    Key Requirements: {', '.join(requirements['key_requirements'])}
    Timeline: {requirements['timeline']}
    Additional Requirements: {additional_requirements}
    Extra Information For Complete Context: {opportunity}
    
    To: {company_name}
    
    Important: 
    - Be professional and reference the government contract opportunity
    - Make sure to mention any product/service, quantities or requrements that need to be mentioned
    - Mention we're seeking competitive quotes from qualified suppliers
    - Ask for detailed pricing breakdown, delivery timeline, and compliance with requirements
    - Request information about relevant past performance
    - DO NOT mention any specific budget or target price
    - Keep under 200 words
    
    Sign as "Procurement Team"
    """
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a professional government procurement specialist."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )
    
    return response.choices[0].message.content

def generate_supplier_response(company_name, requirements, messages, round_num, target_price):
    """Generate supplier response with pricing strategy"""
    
    # Pricing strategy remains internal - never shared with supplier
    if round_num == 0:
        price_multiplier = 1.5 + (hash(company_name) % 30) / 100  # 50-80% above target
    elif round_num == 1:
        price_multiplier = 1.25 + (hash(company_name + str(round_num)) % 20) / 100  # 25-45% above
    else:
        price_multiplier = 1.10 + (hash(company_name + str(round_num)) % 15) / 100  # 10-25% above
    
    suggested_price = target_price * price_multiplier
    
    history = "\n".join([f"{m.sender}: {m.content}" for m in messages[-3:]])
    
    prompt = f"""
    You are a sales representative for {company_name}, responding to a government contract RFP.
    
    Contract Requirements:
    - Product/Service: {requirements['product_service']}
    - Quantity: {requirements['quantity']}
    - Key Requirements: {', '.join(requirements['key_requirements'])}
    - Timeline: {requirements['timeline']}
    
    Previous conversation:
    {history}
    
    This is negotiation round {round_num}.
    Your total pricing should be around ${suggested_price:.2f}.
    
    Guidelines:
    - Round 0: Provide comprehensive initial quote with itemized pricing
    - Round 1: Show flexibility, offer 10-20% discount, mention volume benefits
    - Round 2: Final best offer with smallest additional discount
    
    Include:
    - Itemized cost breakdown
    - Delivery timeline
    - Compliance with requirements
    - Relevant past performance (make it realistic)
    - Payment terms (Net 30 standard for government)
    
    Be professional and detailed. Keep under 250 words.
    """
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an experienced government contractor sales representative."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.8
    )
    
    return response.choices[0].message.content, suggested_price

def generate_negotiation_response(messages, requirements, round_num):
    """Generate buyer's negotiation response that engages with the vendor's actual points"""
    
    # Include full conversation history (truncate individual messages if very long)
    history_parts = []
    for m in messages:
        content = m.content if len(m.content) <= 1500 else m.content[:1500] + "..."
        label = "BUYER" if m.sender == "buyer" else "VENDOR"
        history_parts.append(f"[{label}]: {content}")
    full_history = "\n\n".join(history_parts)
    
    # Extract the vendor's latest message explicitly
    vendor_messages = [m for m in messages if m.sender == 'supplier']
    latest_vendor_msg = vendor_messages[-1].content if vendor_messages else "No vendor response yet."
    
    is_final = round_num >= 2
    
    prompt = f"""You are a government procurement specialist writing a follow-up email to a vendor
about: {requirements.get('product_service', 'a government contract')}.

FULL CONVERSATION SO FAR:
{full_history}

THE VENDOR'S LATEST MESSAGE (you MUST address this directly):
{latest_vendor_msg}

YOUR TASK - Write a professional reply that:
1. FIRST: Directly acknowledge and respond to the specific points, questions, or concerns the vendor raised in their latest message. If they asked questions, answer them. If they made counterpoints, address them.
2. THEN: Naturally steer the conversation toward more favorable pricing or terms.
{"3. This is your final opportunity to negotiate. Ask for their best and final offer." if is_final else "3. Explore whether there is flexibility on pricing, delivery timelines, or added value."}

RULES:
- NEVER reveal specific budget numbers or target prices
- You may reference "budget constraints" or "other competitive proposals"
- Sound like a real person, not a template. Vary your language.
- Be respectful and collaborative, not adversarial
- Keep under 300 words
- Do NOT include subject lines, greetings like "Dear...", or sign-offs like "Best regards" — those are added separately
"""
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an experienced government procurement negotiator. You write natural, professional emails that engage with what the other party actually said. You never sound robotic or repetitive."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.75
    )
    
    return response.choices[0].message.content

def extract_price_from_message(content):
    """Extract total price from supplier message"""
    
    # Look for total price patterns
    patterns = [
        r'total:?\s*\$?([\d,]+\.?\d*)',
        r'total\s*(?:cost|price|amount):?\s*\$?([\d,]+\.?\d*)',
        r'grand\s*total:?\s*\$?([\d,]+\.?\d*)',
        r'\$?([\d,]+\.?\d*)\s*total',
        r'quoted\s*(?:price|amount):?\s*\$?([\d,]+\.?\d*)'
    ]
    
    content_lower = content.lower()
    
    for pattern in patterns:
        matches = re.findall(pattern, content_lower)
        if matches:
            # Get the last match (usually the total)
            price_str = matches[-1].replace(',', '')
            try:
                return float(price_str)
            except:
                continue
    
    # Fallback: look for any large dollar amount
    dollar_amounts = re.findall(r'\$?([\d,]+\.?\d*)', content)
    amounts = []
    for amount in dollar_amounts:
        try:
            val = float(amount.replace(',', ''))
            if val > 100:  # Assume prices are > $100
                amounts.append(val)
        except:
            continue
    
    return max(amounts) if amounts else None

# Routes

@quote_bp.route('/sam-gov/dashboard-stats', methods=['GET'])
def get_dashboard_stats():
    """Get dashboard statistics from negotiations data"""
    try:
        # Get all sessions
        sessions = NegotiationSession.query.all()
        
        # Calculate stats
        total_sessions = len(sessions)
        active_negotiations = sum(1 for s in sessions if s.status == 'active')
        bids_submitted = sum(1 for s in sessions if s.status == 'bid_submitted')
        completed_negotiations = sum(1 for s in sessions if s.status == 'completed')
        
        # Calculate total value (target prices)
        total_target_value = sum(s.target_price for s in sessions if s.target_price)
        
        # Get supplier stats
        all_suppliers = Supplier.query.all()
        total_suppliers = len(all_suppliers)
        suppliers_with_responses = sum(1 for s in all_suppliers if s.status in ['negotiating', 'completed'])
        
        # Calculate savings from completed negotiations
        total_savings = 0
        for supplier in all_suppliers:
            if supplier.initial_price and supplier.final_price:
                total_savings += supplier.initial_price - supplier.final_price
        
        # Get recent activity (sessions in last 7 days)
        from datetime import timedelta
        week_ago = datetime.utcnow() - timedelta(days=7)
        recent_sessions = sum(1 for s in sessions if s.created_at and s.created_at > week_ago)
        
        return jsonify({
            'stats': {
                'active_negotiations': active_negotiations,
                'bids_submitted': bids_submitted,
                'completed_negotiations': completed_negotiations,
                'total_sessions': total_sessions,
                'total_target_value': total_target_value,
                'total_suppliers_engaged': total_suppliers,
                'suppliers_responded': suppliers_with_responses,
                'total_savings': total_savings,
                'recent_activity': recent_sessions
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@quote_bp.route('/sam-gov/sessions', methods=['GET'])
def get_all_sessions():
    """Get all negotiation sessions"""
    try:
        sessions = NegotiationSession.query.order_by(NegotiationSession.created_at.desc()).all()
        
        result = []
        for session in sessions:
            suppliers_data = []
            for supplier in session.suppliers:
                suppliers_data.append({
                    'id': supplier.id,
                    'company_name': supplier.company_name,
                    'status': supplier.status,
                    'negotiation_round': supplier.negotiation_round,
                    'initial_price': supplier.initial_price,
                    'final_price': supplier.final_price
                })
            
            result.append({
                'id': session.id,
                'opportunity_id': session.opportunity_id,
                'opportunity_title': session.opportunity_title,
                'status': session.status,
                'target_price': session.target_price,
                'created_at': session.created_at.isoformat(),
                'suppliers': suppliers_data
            })
        
        return jsonify({'sessions': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@quote_bp.route('/sam-gov/get-ai-suppliers', methods=['POST'])
def get_ai_suppliers():
    """Get AI-suggested suppliers for an opportunity (before creating negotiation session)"""
    
    data = request.json
    opportunity = data.get('opportunity')
    
    if not opportunity:
        return jsonify({'error': 'Opportunity data is required'}), 400
    
    # Extract requirements using AI (includes supplier suggestions)
    requirements = extract_requirements_from_opportunity(opportunity)
    
    ai_suppliers = requirements.get('suggested_suppliers', [])
    
    # Ensure we return the proper format (objects with name and email)
    if ai_suppliers and len(ai_suppliers) > 0:
        if isinstance(ai_suppliers[0], str):
            # Old format - convert to new format
            ai_suppliers = [
                {"name": s, "email": f"quotes@{s.lower().replace(' ', '').replace(',', '').replace('.', '')}.com"} 
                for s in ai_suppliers
            ]
    else:
        # Fallback suppliers
        ai_suppliers = [
            {"name": "Federal Contractors Inc", "email": "quotes@federalcontractors.com"},
            {"name": "Government Solutions LLC", "email": "rfq@govsolutions.com"},
            {"name": "Contract Services Corp", "email": "sales@contractservices.com"},
            {"name": "Public Sector Partners", "email": "bids@publicsector.com"},
            {"name": "Federal Supply Company", "email": "quotes@federalsupply.com"}
        ]
    
    return jsonify({
        'ai_suppliers': ai_suppliers[:5],  # Return exactly 5 suppliers
        'requirements': {
            'product_service': requirements.get('product_service', ''),
            'industry_category': requirements.get('industry_category', 'general')
        }
    })


@quote_bp.route('/sam-gov/negotiate', methods=['POST'])
def create_negotiation():
    """Create new negotiation session from SAM.gov opportunity"""
    
    data = request.json
    opportunity = data['opportunity']
    target_price = data['targetPrice']
    additional_requirements = data.get('additionalRequirements', '')
    selected_ai_suppliers = data.get('selectedAiSuppliers', [])  # List of selected AI supplier objects
    manual_suppliers = data.get('manualSuppliers', [])  # List of manually added suppliers
    
    # Extract requirements using AI
    requirements = extract_requirements_from_opportunity(opportunity)
    
    # Create negotiation session
    session = NegotiationSession(
        opportunity_id=opportunity['id'],
        opportunity_title=opportunity.get('title', 'Government Contract'),
        opportunity_data=json.dumps(opportunity),
        target_price=target_price,
        extracted_requirements=json.dumps(requirements)
    )
    db.session.add(session)
    db.session.commit()
    
    initial_messages = []
    suppliers_data = []
    
    # Process selected AI suppliers
    for supplier_info in selected_ai_suppliers:
        # Handle both old format (string) and new format (object with name/email)
        if isinstance(supplier_info, str):
            name = supplier_info
            email = None
        else:
            name = supplier_info.get('name', 'Unknown Supplier')
            email = supplier_info.get('email', '')
        
        supplier = Supplier(
            session_id=session.id,
            company_name=name,
            email=email,
            is_manual=False,
            industry=requirements.get('industry_category', 'general'),
            status='pending'
        )
        db.session.add(supplier)
        db.session.commit()
        
        # Generate draft initial message
        draft_message = generate_initial_request(
            opportunity,
            requirements, 
            name,
            additional_requirements
        )
        
        initial_messages.append({
            'supplier_id': supplier.id,
            'supplier_name': name,
            'supplier_email': email,
            'draft_message': draft_message,
            'is_manual': False
        })
        
        suppliers_data.append({
            'id': supplier.id,
            'company_name': supplier.company_name,
            'email': supplier.email,
            'is_manual': False,
            'status': supplier.status,
            'negotiation_round': supplier.negotiation_round,
            'messages': []
        })
    
    # Process manually added suppliers
    for manual_supplier in manual_suppliers:
        name = manual_supplier.get('name', 'Manual Supplier')
        email = manual_supplier.get('email', '')
        notes = manual_supplier.get('notes', '')
        
        supplier = Supplier(
            session_id=session.id,
            company_name=name,
            email=email,
            notes=notes,
            is_manual=True,
            industry=requirements.get('industry_category', 'general'),
            status='pending'
        )
        db.session.add(supplier)
        db.session.commit()
        
        # Generate draft initial message for manual supplier
        draft_message = generate_initial_request(
            opportunity,
            requirements, 
            name,
            additional_requirements + (f"\n\nNote: {notes}" if notes else "")
        )
        
        initial_messages.append({
            'supplier_id': supplier.id,
            'supplier_name': name,
            'supplier_email': email,
            'draft_message': draft_message,
            'is_manual': True
        })
        
        suppliers_data.append({
            'id': supplier.id,
            'company_name': supplier.company_name,
            'email': supplier.email,
            'notes': notes,
            'is_manual': True,
            'status': supplier.status,
            'negotiation_round': supplier.negotiation_round,
            'messages': []
        })
    
    db.session.commit()
    
    # Return session data with AI suggested suppliers for frontend to display
    ai_suppliers = requirements.get('suggested_suppliers', [])
    # Ensure we return the proper format
    if ai_suppliers and isinstance(ai_suppliers[0], str):
        # Old format - convert to new format
        ai_suppliers = [{"name": s, "email": f"contact@{s.lower().replace(' ', '')}.com"} for s in ai_suppliers]
    
    return jsonify({
        'id': session.id,
        'opportunity_id': session.opportunity_id,
        'status': session.status,
        'created_at': session.created_at.isoformat(),
        'ai_suggested_suppliers': ai_suppliers,  # Send AI suggestions to frontend for selection
        'initial_messages': initial_messages,
        'suppliers': suppliers_data
    })

@quote_bp.route('/negotiate/<int:session_id>')
def get_negotiation_status(session_id):
    """Get current negotiation status"""
    
    session = NegotiationSession.query.get_or_404(session_id)
    
    return jsonify({
        'id': session.id,
        'opportunity_id': session.opportunity_id,
        'status': session.status,
        'created_at': session.created_at.isoformat(),
        'suppliers': [{
            'id': s.id,
            'company_name': s.company_name,
            'email': s.email,
            'notes': s.notes,
            'is_manual': s.is_manual,
            'email_sent': s.email_sent or False,
            'status': s.status,
            'negotiation_round': s.negotiation_round,
            'messages': [{
                'sender': m.sender,
                'content': m.content,
                'price_mentioned': m.price_mentioned,
                'created_at': m.created_at.isoformat()
            } for m in s.messages],
            'metrics': {
                'initial_price': f"{s.initial_price:.2f}",
                'final_price': f"{s.final_price:.2f}",
                'savings': f"{s.initial_price - s.final_price:.2f}",
                'savings_percent': f"{((s.initial_price - s.final_price) / s.initial_price * 100):.1f}"
            } if s.status == 'completed' and s.initial_price and s.final_price else None
        } for s in session.suppliers]
    })

# @quote_bp.route('/negotiate/<int:session_id>/respond/<int:supplier_id>', methods=['POST'])
# def respond_to_supplier(session_id, supplier_id):
#     """Generate response to supplier"""
    
#     session = NegotiationSession.query.get_or_404(session_id)
#     supplier = Supplier.query.get_or_404(supplier_id)
#     requirements = json.loads(session.extracted_requirements)
    
#     # Check if this is initial response or negotiation
#     supplier_messages = Message.query.filter_by(
#         supplier_id=supplier_id, sender='supplier'
#     ).count()
    
#     if supplier_messages == 0:
#         # Generate initial quote
#         response, price = generate_supplier_response(
#             supplier.company_name,
#             requirements,
#             supplier.messages,
#             0,
#             session.target_price
#         )
        
#         supplier.initial_price = price
#         supplier.status = 'negotiating'
#     else:
#         # Generate negotiation response
#         response, price = generate_supplier_response(
#             supplier.company_name,
#             requirements,
#             supplier.messages,
#             supplier.negotiation_round,
#             session.target_price
#         )
    
#     # Save supplier message
#     msg = Message(
#         supplier_id=supplier_id,
#         sender='supplier',
#         content=response,
#         price_mentioned=extract_price_from_message(response) or price
#     )
#     db.session.add(msg)
    
#     # Auto-generate buyer response if not final round
#     if supplier.negotiation_round < 2:
#         supplier.negotiation_round += 1
        
#         # Generate buyer's negotiation response
#         buyer_response = generate_negotiation_response(
#             supplier.messages + [msg],
#             requirements,
#             supplier.negotiation_round
#         )
        
#         buyer_msg = Message(
#             supplier_id=supplier_id,
#             sender='buyer',
#             content=buyer_response
#         )
#         db.session.add(buyer_msg)
#     else:
#         # Final round - update final price
#         supplier.final_price = extract_price_from_message(response) or price
    
#     db.session.commit()
#     return jsonify({'success': True})


@quote_bp.route('/negotiate/<int:session_id>/respond/<int:supplier_id>', methods=['POST'])
def respond_to_supplier(session_id, supplier_id):
    """Generate supplier response only - no auto buyer response"""
    
    session = NegotiationSession.query.get_or_404(session_id)
    supplier = Supplier.query.get_or_404(supplier_id)
    requirements = json.loads(session.extracted_requirements)
    
    # If a real email was sent, don't generate simulated responses.
    # Wait for the email poller to pick up the real vendor reply.
    if supplier.email_sent:
        existing_supplier_msgs = Message.query.filter_by(
            supplier_id=supplier_id, sender='supplier'
        ).order_by(Message.created_at.desc()).all()
        
        buyer_msg_count = Message.query.filter_by(
            supplier_id=supplier_id, sender='buyer'
        ).count()
        
        if len(existing_supplier_msgs) >= buyer_msg_count:
            latest = existing_supplier_msgs[0]
            return jsonify({
                'success': True,
                'supplier_message': {
                    'content': latest.content,
                    'price_mentioned': latest.price_mentioned
                }
            })
        
        return jsonify({
            'success': False,
            'waiting_for_email': True,
            'message': 'Waiting for vendor to reply by email'
        })
    
    # No real email sent — generate AI-simulated response
    supplier_messages = Message.query.filter_by(
        supplier_id=supplier_id, sender='supplier'
    ).count()
    
    if supplier_messages == 0:
        response, price = generate_supplier_response(
            supplier.company_name,
            requirements,
            supplier.messages,
            0,
            session.target_price
        )
        
        supplier.initial_price = price
        supplier.status = 'negotiating'
    else:
        response, price = generate_supplier_response(
            supplier.company_name,
            requirements,
            supplier.messages,
            supplier.negotiation_round,
            session.target_price
        )
    
    msg = Message(
        supplier_id=supplier_id,
        sender='supplier',
        content=response,
        price_mentioned=extract_price_from_message(response) or price
    )
    db.session.add(msg)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'supplier_message': {
            'content': response,
            'price_mentioned': extract_price_from_message(response) or price
        }
    })

# Add these new routes to your existing quote_bp

# Add this route to your quote_bp in the Python file

@quote_bp.route('/negotiate/<int:session_id>/send-initial/<int:supplier_id>', methods=['POST'])
def send_initial_message(session_id, supplier_id):
    """Send initial message to supplier (allows user to edit before sending)"""
    
    data = request.json
    message_content = data.get('content')
    send_email = data.get('send_email', True)  # Option to actually send email
    
    if not message_content:
        return jsonify({'error': 'Message content is required'}), 400
    
    session = NegotiationSession.query.get_or_404(session_id)
    supplier = Supplier.query.get_or_404(supplier_id)
    
    # Check if initial message already exists
    existing_messages = Message.query.filter_by(
        supplier_id=supplier_id
    ).count()
    
    if existing_messages > 0:
        # Clear existing messages if we're resending initial
        Message.query.filter_by(supplier_id=supplier_id).delete()
    
    # Save the initial message
    msg = Message(
        supplier_id=supplier_id,
        sender='buyer',
        content=message_content
    )
    db.session.add(msg)
    
    # Set supplier status
    supplier.status = 'pending'
    supplier.negotiation_round = 0
    
    email_result = None
    
    # Send actual email if enabled and supplier has email
    if send_email and supplier.email:
        try:
            opportunity_data = json.loads(session.opportunity_data) if session.opportunity_data else {}
            
            email_result = send_rfq_email(
                to_email=supplier.email,
                vendor_name=supplier.company_name,
                subject=f"Request for Quotation - {session.opportunity_title or 'Government Contract Opportunity'}",
                rfq_content=message_content,
                opportunity_title=session.opportunity_title,
                opportunity_id=session.opportunity_id,
                deadline=opportunity_data.get('closingDate', None)
            )
            
            if email_result['success']:
                supplier.email_sent = True
                supplier.email_sent_at = datetime.utcnow()
                supplier.last_email_message_id = email_result.get('message_id')
                print(f"[SendInitial] Email to {supplier.email}: SUCCESS, message_id={email_result.get('message_id')}")
            else:
                print(f"[SendInitial] Email to {supplier.email}: FAILED - {email_result.get('error')}")
        except Exception as e:
            email_result = {'success': False, 'error': str(e)}
            print(f"[SendInitial] Email to {supplier.email}: EXCEPTION - {e}")
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': {
            'id': msg.id,
            'content': message_content,
            'created_at': msg.created_at.isoformat()
        },
        'email_sent': email_result['success'] if email_result else False,
        'email_error': email_result.get('error') if email_result and not email_result['success'] else None
    })

@quote_bp.route('/negotiate/<int:session_id>/get-supplier-response/<int:supplier_id>', methods=['POST'])
def get_supplier_response(session_id, supplier_id):
    """Generate and save supplier response only"""
    
    session = NegotiationSession.query.get_or_404(session_id)
    supplier = Supplier.query.get_or_404(supplier_id)
    requirements = json.loads(session.extracted_requirements)
    
    buyer_messages = Message.query.filter_by(
        supplier_id=supplier_id, sender='buyer'
    ).count()
    
    if buyer_messages == 0:
        return jsonify({'error': 'No initial message sent to supplier yet'}), 400
    
    # If a real email was sent, don't generate simulated responses.
    # The email poller will create supplier messages when the vendor replies.
    if supplier.email_sent:
        existing_supplier_msgs = Message.query.filter_by(
            supplier_id=supplier_id, sender='supplier'
        ).order_by(Message.created_at.desc()).all()
        
        if len(existing_supplier_msgs) >= buyer_messages:
            latest = existing_supplier_msgs[0]
            return jsonify({
                'success': True,
                'supplier_message': {
                    'id': latest.id,
                    'content': latest.content,
                    'price_mentioned': latest.price_mentioned,
                    'created_at': latest.created_at.isoformat()
                },
                'can_negotiate': supplier.negotiation_round < 2
            })
        
        return jsonify({
            'success': False,
            'waiting_for_email': True,
            'message': 'Waiting for vendor to reply by email'
        })
    
    # No real email sent — generate AI-simulated response
    supplier_messages = Message.query.filter_by(
        supplier_id=supplier_id, sender='supplier'
    ).count()
    
    if supplier_messages == 0:
        response, price = generate_supplier_response(
            supplier.company_name,
            requirements,
            supplier.messages,
            0,
            session.target_price
        )
        
        supplier.initial_price = price
        supplier.status = 'negotiating'
    else:
        response, price = generate_supplier_response(
            supplier.company_name,
            requirements,
            supplier.messages,
            supplier.negotiation_round,
            session.target_price
        )
    
    msg = Message(
        supplier_id=supplier_id,
        sender='supplier',
        content=response,
        price_mentioned=extract_price_from_message(response) or price
    )
    db.session.add(msg)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'supplier_message': {
            'id': msg.id,
            'content': response,
            'price_mentioned': extract_price_from_message(response) or price,
            'created_at': msg.created_at.isoformat()
        },
        'can_negotiate': supplier.negotiation_round < 2
    })

@quote_bp.route('/negotiate/<int:session_id>/draft/<int:supplier_id>', methods=['POST'])
def generate_draft_response(session_id, supplier_id):
    """Generate a draft buyer response for user to review/edit"""
    
    session = NegotiationSession.query.get_or_404(session_id)
    supplier = Supplier.query.get_or_404(supplier_id)
    requirements = json.loads(session.extracted_requirements)
    
    # Get all messages including the latest supplier message
    messages = Message.query.filter_by(supplier_id=supplier_id).order_by(Message.created_at).all()
    
    # Determine negotiation round
    supplier_message_count = len([m for m in messages if m.sender == 'supplier'])
    current_round = min(supplier_message_count, 2)  # Max 2 rounds of negotiation
    
    # Generate draft response
    draft_response = generate_negotiation_response(
        messages,
        requirements,
        current_round
    )
    
    return jsonify({
        'success': True,
        'draft': draft_response,
        'round': current_round,
        'is_final_round': current_round >= 2
    })

@quote_bp.route('/negotiate/<int:session_id>/send/<int:supplier_id>', methods=['POST'])
def send_buyer_response(session_id, supplier_id):
    """Send the user's edited response to supplier"""
    
    data = request.json
    response_content = data.get('content')
    send_email = data.get('send_email', True)  # Option to actually send email
    
    if not response_content:
        return jsonify({'error': 'Response content is required'}), 400
    
    session = NegotiationSession.query.get_or_404(session_id)
    supplier = Supplier.query.get_or_404(supplier_id)
    
    # Save the buyer's message
    msg = Message(
        supplier_id=supplier_id,
        sender='buyer',
        content=response_content
    )
    db.session.add(msg)
    
    # Update negotiation round
    supplier_message_count = Message.query.filter_by(
        supplier_id=supplier_id, sender='supplier'
    ).count()
    supplier.negotiation_round = min(supplier_message_count, 2)
    
    email_result = None
    
    # Send negotiation email if enabled and supplier has email
    if send_email and supplier.email:
        try:
            email_result = send_negotiation_email(
                to_email=supplier.email,
                vendor_name=supplier.company_name,
                subject=f"Re: Request for Quotation - {session.opportunity_title or 'Government Contract'}",
                negotiation_content=response_content,
                round_number=supplier.negotiation_round,
                opportunity_title=session.opportunity_title,
                in_reply_to=supplier.last_email_message_id
            )
            
            if email_result['success']:
                supplier.email_sent_at = datetime.utcnow()
                supplier.last_email_message_id = email_result.get('message_id')
        except Exception as e:
            email_result = {'success': False, 'error': str(e)}
    
    # If this is the final round, mark for completion
    if supplier.negotiation_round >= 2:
        # Get the last supplier price for final price
        last_supplier_msg = Message.query.filter_by(
            supplier_id=supplier_id, sender='supplier'
        ).order_by(Message.created_at.desc()).first()
        
        if last_supplier_msg and last_supplier_msg.price_mentioned:
            supplier.final_price = last_supplier_msg.price_mentioned
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'negotiation_round': supplier.negotiation_round,
        'can_continue': supplier.negotiation_round < 2,
        'email_sent': email_result['success'] if email_result else False,
        'email_error': email_result.get('error') if email_result and not email_result['success'] else None
    })

@quote_bp.route('/negotiate/<int:session_id>/status', methods=['POST'])
def update_session_status(session_id):
    """Update session status"""
    data = request.json
    new_status = data.get('status')
    
    if new_status not in ['active', 'completed', 'bid_submitted', 'cancelled']:
        return jsonify({'error': 'Invalid status'}), 400
    
    session = NegotiationSession.query.get_or_404(session_id)
    session.status = new_status
    db.session.commit()
    
    return jsonify({'success': True, 'status': session.status})


@quote_bp.route('/negotiate/<int:session_id>/accept/<int:supplier_id>', methods=['POST'])
def accept_quote(session_id, supplier_id):
    """Accept a supplier's quote"""
    from notifications import create_negotiation_complete_notification
    
    session = NegotiationSession.query.get_or_404(session_id)
    supplier = Supplier.query.get_or_404(supplier_id)
    supplier.status = 'completed'
    
    # Set final price from last message
    last_supplier_msg = Message.query.filter_by(
        supplier_id=supplier_id, sender='supplier'
    ).order_by(Message.created_at.desc()).first()
    
    if last_supplier_msg and last_supplier_msg.price_mentioned:
        supplier.final_price = last_supplier_msg.price_mentioned
    
    db.session.commit()
    
    # Create notification for completed negotiation
    try:
        create_negotiation_complete_notification(
            vendor_name=supplier.company_name,
            opportunity_title=session.opportunity_title,
            final_price=supplier.final_price or 0,
            session_id=session_id,
            supplier_id=supplier_id,
            send_email=False  # Can be enabled with user email
        )
    except Exception as e:
        print(f"Failed to create notification: {e}")
    
    return jsonify({'success': True})


@quote_bp.route('/negotiate/<int:session_id>/recommendations', methods=['GET'])
def get_vendor_recommendations(session_id):
    """Get AI-powered vendor comparison and recommendations"""
    
    session = NegotiationSession.query.get_or_404(session_id)
    suppliers = Supplier.query.filter_by(session_id=session_id).all()
    requirements = json.loads(session.extracted_requirements) if session.extracted_requirements else {}
    
    if not suppliers:
        return jsonify({'error': 'No suppliers found for this session'}), 404
    
    # Collect vendor data for comparison
    vendor_data = []
    for supplier in suppliers:
        messages = Message.query.filter_by(supplier_id=supplier.id).order_by(Message.created_at).all()
        
        # Get latest price from messages
        latest_price = None
        for msg in reversed(messages):
            if msg.sender == 'supplier' and msg.price_mentioned:
                latest_price = msg.price_mentioned
                break
        
        # Use final_price if available, otherwise latest mentioned price
        price = supplier.final_price or latest_price or supplier.initial_price
        
        vendor_data.append({
            'id': supplier.id,
            'company_name': supplier.company_name,
            'email': supplier.email,
            'is_manual': supplier.is_manual,
            'status': supplier.status,
            'initial_price': supplier.initial_price,
            'final_price': supplier.final_price,
            'current_price': price,
            'negotiation_round': supplier.negotiation_round,
            'message_count': len(messages),
            'last_message': messages[-1].content if messages else None
        })
    
    # Generate AI recommendations
    recommendations = generate_vendor_recommendations(
        vendor_data=vendor_data,
        requirements=requirements,
        target_price=session.target_price,
        opportunity_title=session.opportunity_title
    )
    
    return jsonify({
        'session_id': session_id,
        'opportunity_title': session.opportunity_title,
        'target_price': session.target_price,
        'vendor_count': len(suppliers),
        'vendors': vendor_data,
        'recommendations': recommendations
    })


def generate_vendor_recommendations(vendor_data, requirements, target_price, opportunity_title):
    """Use AI to generate vendor recommendations"""
    
    # Build comparison prompt
    vendor_summary = "\n".join([
        f"- {v['company_name']}: Price ${v['current_price']:.2f}, Status: {v['status']}, Rounds: {v['negotiation_round']}" 
        if v['current_price'] is not None 
        else f"- {v['company_name']}: Price N/A, Status: {v['status']}, Rounds: {v['negotiation_round']}"
        for v in vendor_data
    ])
    
    prompt = f"""
    Analyze the following vendor quotes for a government contract opportunity and provide recommendations.
    
    Opportunity: {opportunity_title}
    Target Budget: ${target_price:.2f}
    
    Requirements:
    - Product/Service: {requirements.get('product_service', 'As specified')}
    - Key Requirements: {', '.join(requirements.get('key_requirements', ['Meet RFP requirements']))}
    - Timeline: {requirements.get('timeline', 'As per contract')}
    - Certifications Needed: {', '.join(requirements.get('certifications_needed', ['As required']))}
    
    Vendor Quotes:
    {vendor_summary}
    
    Please provide:
    1. BEST_VENDOR: The recommended vendor (company name)
    2. RANKING: All vendors ranked by overall value (list of company names, best first)
    3. PRICE_ANALYSIS: Brief analysis of pricing vs target budget
    4. RISK_FACTORS: Any concerns or risks with top vendors
    5. RECOMMENDATION_REASONING: Why the best vendor is recommended
    6. SAVINGS_POTENTIAL: Estimated savings compared to target if accepting recommended vendor
    
    Return as JSON with these exact keys: best_vendor, ranking, price_analysis, risk_factors, recommendation_reasoning, savings_potential
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert government procurement analyst. Provide objective vendor recommendations based on price, compliance, and value."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        
        content = response.choices[0].message.content
        
        # Clean up markdown code blocks if present
        if content.startswith('```'):
            content = content.split('```')[1]
            if content.startswith('json'):
                content = content[4:]
        
        recommendations = json.loads(content.strip())
        
        # Add scores for each vendor
        vendor_scores = []
        for v in vendor_data:
            if v['current_price'] and target_price:
                price_score = max(0, 100 - abs(v['current_price'] - target_price) / target_price * 100)
            else:
                price_score = 50  # Default score if no price
            
            # Calculate overall score
            status_score = 100 if v['status'] == 'completed' else (75 if v['status'] == 'negotiating' else 50)
            negotiation_score = min(100, v['negotiation_round'] * 30 + 40)  # More rounds = more engaged
            
            overall_score = (price_score * 0.5) + (status_score * 0.3) + (negotiation_score * 0.2)
            
            vendor_scores.append({
                'vendor_id': v['id'],
                'company_name': v['company_name'],
                'price_score': round(price_score, 1),
                'status_score': status_score,
                'engagement_score': negotiation_score,
                'overall_score': round(overall_score, 1),
                'is_recommended': v['company_name'] == recommendations.get('best_vendor')
            })
        
        # Sort by overall score
        vendor_scores.sort(key=lambda x: x['overall_score'], reverse=True)
        
        recommendations['vendor_scores'] = vendor_scores
        
        return recommendations
        
    except Exception as e:
        # Fallback to simple price-based recommendation
        valid_vendors = [v for v in vendor_data if v['current_price']]
        if valid_vendors:
            best = min(valid_vendors, key=lambda x: x['current_price'])
            return {
                'best_vendor': best['company_name'],
                'ranking': [v['company_name'] for v in sorted(valid_vendors, key=lambda x: x['current_price'])],
                'price_analysis': f"Lowest price is ${best['current_price']:.2f} from {best['company_name']}",
                'risk_factors': ['Unable to perform detailed AI analysis'],
                'recommendation_reasoning': 'Based on lowest price',
                'savings_potential': f"${target_price - best['current_price']:.2f}" if target_price else 'N/A',
                'vendor_scores': [],
                'error': str(e)
            }
        return {
            'best_vendor': None,
            'ranking': [],
            'price_analysis': 'No valid prices available for comparison',
            'risk_factors': ['No pricing data available'],
            'recommendation_reasoning': 'Unable to make recommendation without pricing data',
            'savings_potential': 'N/A',
            'vendor_scores': [],
            'error': str(e)
        }
