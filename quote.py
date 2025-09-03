from flask import Blueprint, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from openai import OpenAI
import os
import json
import re
from dotenv import load_dotenv

load_dotenv()

# Create Blueprint
quote_bp = Blueprint('quote', __name__, url_prefix='/api')

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

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
        industry = db.Column(db.String(100))
        initial_price = db.Column(db.Float)
        final_price = db.Column(db.Float)
        status = db.Column(db.String(50), default='pending')
        negotiation_round = db.Column(db.Integer, default=0)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        
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
    8. "suggested_suppliers": List of 5-7 realistic supplier company names for this type of work
    
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
        return json.loads(response.choices[0].message.content)
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
                "Federal Contractors Inc", "Government Solutions LLC", 
                "Contract Services Corp", "Public Sector Partners",
                "Federal Supply Company"
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
    """Generate buyer's negotiation response without revealing target price"""
    
    history = "\n".join([f"{m.sender}: {m.content}" for m in messages[-3:]])
    
    prompt = f"""
    You are negotiating a government contract for: {requirements['product_service']}
    This is negotiation round {round_num} of maximum 2.
    
    Previous conversation:
    {history}
    
    Guidelines:
    - Round 1: Express that pricing exceeds available budget, ask for better terms
    - Round 2: Final negotiation, mention competitive bids received, ask for best and final
    
    Important:
    - NEVER mention specific budget numbers or target prices
    - Reference "budget constraints" or "competitive pricing" instead
    - Mention you're evaluating multiple qualified suppliers
    - Ask about additional value adds or services
    
    Be professional and keep under 150 words.
    """
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a government procurement negotiator. Never reveal budget numbers."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
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
@quote_bp.route('/sam-gov/negotiate', methods=['POST'])
def create_negotiation():
    """Create new negotiation session from SAM.gov opportunity"""
    
    data = request.json
    opportunity = data['opportunity']
    target_price = data['targetPrice']
    additional_requirements = data.get('additionalRequirements', '')
    num_suppliers = data.get('numSuppliers', 5)
    
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
    
    # Create suppliers based on AI suggestions
    supplier_names = requirements.get('suggested_suppliers', [])[:num_suppliers]
    
    # Ensure we have enough supplier names
    while len(supplier_names) < num_suppliers:
        supplier_names.append(f"Qualified Contractor #{len(supplier_names) + 1}")
    
    for name in supplier_names:
        supplier = Supplier(
            session_id=session.id,
            company_name=name,
            industry=requirements.get('industry_category', 'general')
        )
        db.session.add(supplier)
        db.session.commit()
        
        # Generate initial request
        initial_message = generate_initial_request(
            opportunity,
            requirements, 
            name,
            additional_requirements
        )
        
        message = Message(
            supplier_id=supplier.id,
            sender='buyer',
            content=initial_message
        )
        db.session.add(message)
    
    db.session.commit()
    
    # Return session data
    return jsonify({
        'id': session.id,
        'opportunity_id': session.opportunity_id,
        'status': session.status,
        'created_at': session.created_at.isoformat(),
        'suppliers': [{
            'id': s.id,
            'company_name': s.company_name,
            'status': s.status,
            'negotiation_round': s.negotiation_round,
            'messages': [{
                'sender': m.sender,
                'content': m.content,
                'price_mentioned': m.price_mentioned
            } for m in s.messages]
        } for s in session.suppliers]
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

@quote_bp.route('/negotiate/<int:session_id>/respond/<int:supplier_id>', methods=['POST'])
def respond_to_supplier(session_id, supplier_id):
    """Generate response to supplier"""
    
    session = NegotiationSession.query.get_or_404(session_id)
    supplier = Supplier.query.get_or_404(supplier_id)
    requirements = json.loads(session.extracted_requirements)
    
    # Check if this is initial response or negotiation
    supplier_messages = Message.query.filter_by(
        supplier_id=supplier_id, sender='supplier'
    ).count()
    
    if supplier_messages == 0:
        # Generate initial quote
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
        # Generate negotiation response
        response, price = generate_supplier_response(
            supplier.company_name,
            requirements,
            supplier.messages,
            supplier.negotiation_round,
            session.target_price
        )
    
    # Save supplier message
    msg = Message(
        supplier_id=supplier_id,
        sender='supplier',
        content=response,
        price_mentioned=extract_price_from_message(response) or price
    )
    db.session.add(msg)
    
    # Auto-generate buyer response if not final round
    if supplier.negotiation_round < 2:
        supplier.negotiation_round += 1
        
        # Generate buyer's negotiation response
        buyer_response = generate_negotiation_response(
            supplier.messages + [msg],
            requirements,
            supplier.negotiation_round
        )
        
        buyer_msg = Message(
            supplier_id=supplier_id,
            sender='buyer',
            content=buyer_response
        )
        db.session.add(buyer_msg)
    else:
        # Final round - update final price
        supplier.final_price = extract_price_from_message(response) or price
    
    db.session.commit()
    return jsonify({'success': True})

@quote_bp.route('/negotiate/<int:session_id>/accept/<int:supplier_id>', methods=['POST'])
def accept_quote(session_id, supplier_id):
    """Accept a supplier's quote"""
    
    supplier = Supplier.query.get_or_404(supplier_id)
    supplier.status = 'completed'
    
    # Set final price from last message
    last_supplier_msg = Message.query.filter_by(
        supplier_id=supplier_id, sender='supplier'
    ).order_by(Message.created_at.desc()).first()
    
    if last_supplier_msg and last_supplier_msg.price_mentioned:
        supplier.final_price = last_supplier_msg.price_mentioned
    
    db.session.commit()
    return jsonify({'success': True})