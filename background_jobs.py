"""
Background Jobs Module
Handles autonomous negotiation processing using APScheduler
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
import json
import os
from dotenv import load_dotenv
from email_poller import init_poller_db, poll_inbox

load_dotenv()

# Scheduler instance
scheduler = None

# Database references (set by init function)
db = None
Supplier = None
Message = None
NegotiationSession = None


def init_background_jobs(database, supplier_model, message_model, session_model, app_context):
    """Initialize the scheduler with database models"""
    global scheduler, db, Supplier, Message, NegotiationSession
    
    db = database
    Supplier = supplier_model
    Message = message_model
    NegotiationSession = session_model
    
    # Initialize email poller with database models
    init_poller_db(database, supplier_model, message_model, session_model)
    
    # Create scheduler
    scheduler = BackgroundScheduler(daemon=True)
    
    # Add jobs
    # Check for pending negotiations every 5 minutes
    scheduler.add_job(
        func=lambda: run_with_context(app_context, process_pending_negotiations),
        trigger=IntervalTrigger(minutes=5),
        id='process_negotiations',
        name='Process pending negotiations',
        replace_existing=True
    )
    
    # Check for completed negotiations every 10 minutes
    scheduler.add_job(
        func=lambda: run_with_context(app_context, check_completed_negotiations),
        trigger=IntervalTrigger(minutes=10),
        id='check_completions',
        name='Check completed negotiations',
        replace_existing=True
    )
    
    # Poll Gmail inbox for vendor replies
    poll_interval = int(os.getenv('EMAIL_POLL_INTERVAL_SECONDS', '60'))
    scheduler.add_job(
        func=lambda: run_with_context(app_context, poll_inbox),
        trigger=IntervalTrigger(seconds=poll_interval),
        id='poll_email_inbox',
        name='Poll Gmail inbox for vendor replies',
        replace_existing=True
    )
    
    # Start the scheduler
    scheduler.start()
    print("Background scheduler started (with email polling)")
    
    return scheduler


def run_with_context(app_context, func):
    """Run a function within Flask app context"""
    with app_context():
        func()


def process_pending_negotiations():
    """
    Process suppliers awaiting negotiation responses.
    This runs autonomously to continue negotiation rounds.
    """
    from quote import generate_negotiation_response, generate_supplier_response, extract_price_from_message
    from email_service import send_negotiation_email
    from notifications import create_vendor_response_notification
    
    print(f"[{datetime.now()}] Processing pending negotiations...")
    
    try:
        # Find suppliers that need processing
        # Status: 'negotiating' with pending buyer response
        pending_suppliers = Supplier.query.filter(
            Supplier.status == 'negotiating',
            Supplier.negotiation_round < 2
        ).all()
        
        for supplier in pending_suppliers:
            try:
                process_supplier_negotiation(supplier)
            except Exception as e:
                print(f"Error processing supplier {supplier.id}: {e}")
                continue
        
        print(f"[{datetime.now()}] Processed {len(pending_suppliers)} suppliers")
        
    except Exception as e:
        print(f"Error in process_pending_negotiations: {e}")


def process_supplier_negotiation(supplier):
    """
    Process a single supplier's negotiation.
    Determines next action based on message state.
    """
    from quote import generate_negotiation_response, generate_supplier_response, extract_price_from_message
    from email_service import send_negotiation_email
    
    # Get session and requirements
    session = NegotiationSession.query.get(supplier.session_id)
    if not session:
        return
    
    requirements = json.loads(session.extracted_requirements) if session.extracted_requirements else {}
    
    # Count messages
    messages = Message.query.filter_by(supplier_id=supplier.id).order_by(Message.created_at).all()
    buyer_count = len([m for m in messages if m.sender == 'buyer'])
    supplier_count = len([m for m in messages if m.sender == 'supplier'])
    
    # Determine state
    if buyer_count == 0:
        # No initial message sent yet - skip
        return
    
    if buyer_count > supplier_count:
        # Waiting for supplier response - simulate in dev mode
        if os.getenv('AUTO_SIMULATE_RESPONSES', 'false').lower() == 'true':
            simulate_supplier_response(supplier, session, requirements, messages)
    
    elif supplier_count >= buyer_count and supplier.negotiation_round < 2:
        # Need to send buyer counter-offer
        if should_auto_respond(supplier, messages):
            send_auto_counter_offer(supplier, session, requirements, messages)


def should_auto_respond(supplier, messages):
    """
    Determine if we should auto-respond to a supplier.
    Returns True if enough time has passed since last message.
    """
    if not messages:
        return False
    
    last_message = messages[-1]
    
    # Only auto-respond to supplier messages
    if last_message.sender != 'supplier':
        return False
    
    # Check if enough time has passed (configurable, default 1 hour)
    auto_respond_delay = int(os.getenv('AUTO_RESPOND_DELAY_MINUTES', '60'))
    time_since_last = datetime.utcnow() - last_message.created_at
    
    return time_since_last > timedelta(minutes=auto_respond_delay)


def simulate_supplier_response(supplier, session, requirements, messages):
    """
    Simulate a supplier response (for development/demo purposes).
    In production, responses come via email webhook.
    """
    from quote import generate_supplier_response, extract_price_from_message
    from notifications import create_vendor_response_notification
    
    print(f"Simulating response for supplier {supplier.id}: {supplier.company_name}")
    
    try:
        # Generate simulated response
        response_content, suggested_price = generate_supplier_response(
            supplier.company_name,
            requirements,
            messages,
            supplier.negotiation_round,
            session.target_price
        )
        
        # Create message
        msg = Message(
            supplier_id=supplier.id,
            sender='supplier',
            content=response_content,
            price_mentioned=extract_price_from_message(response_content) or suggested_price
        )
        db.session.add(msg)
        
        # Update supplier
        if not supplier.initial_price and suggested_price:
            supplier.initial_price = suggested_price
        
        db.session.commit()
        
        # Create notification
        create_vendor_response_notification(
            vendor_name=supplier.company_name,
            opportunity_title=session.opportunity_title,
            session_id=session.id,
            supplier_id=supplier.id,
            round_number=supplier.negotiation_round,
            opportunity_id=session.opportunity_id
        )
        
        print(f"Simulated response created for supplier {supplier.id}")
        
    except Exception as e:
        db.session.rollback()
        print(f"Error simulating response: {e}")


def send_auto_counter_offer(supplier, session, requirements, messages):
    """
    Automatically generate and send a counter-offer to the supplier.
    """
    from quote import generate_negotiation_response
    from email_service import send_negotiation_email
    
    print(f"Sending auto counter-offer to supplier {supplier.id}: {supplier.company_name}")
    
    try:
        # Generate counter-offer
        supplier.negotiation_round += 1
        counter_content = generate_negotiation_response(
            messages,
            requirements,
            supplier.negotiation_round
        )
        
        # Save message
        msg = Message(
            supplier_id=supplier.id,
            sender='buyer',
            content=counter_content
        )
        db.session.add(msg)
        
        # Send email if enabled
        if os.getenv('AUTO_SEND_EMAILS', 'false').lower() == 'true' and supplier.email:
            email_result = send_negotiation_email(
                to_email=supplier.email,
                vendor_name=supplier.company_name,
                subject=f"Re: Request for Quotation - {session.opportunity_title}",
                negotiation_content=counter_content,
                round_number=supplier.negotiation_round,
                opportunity_title=session.opportunity_title,
                in_reply_to=supplier.last_email_message_id
            )
            
            if email_result['success']:
                supplier.email_sent_at = datetime.utcnow()
                supplier.last_email_message_id = email_result.get('message_id')
        
        db.session.commit()
        print(f"Counter-offer sent to supplier {supplier.id}")
        
    except Exception as e:
        db.session.rollback()
        print(f"Error sending counter-offer: {e}")


def check_completed_negotiations():
    """
    Check for negotiations that have completed all rounds.
    Send notifications for completed negotiations.
    """
    from notifications import create_negotiation_complete_notification
    
    print(f"[{datetime.now()}] Checking for completed negotiations...")
    
    try:
        # Find suppliers that have completed negotiations
        # Either round >= 2 or explicitly marked complete
        completed_suppliers = Supplier.query.filter(
            Supplier.status == 'negotiating',
            Supplier.negotiation_round >= 2
        ).all()
        
        for supplier in completed_suppliers:
            try:
                # Get final price from last supplier message
                last_supplier_msg = Message.query.filter_by(
                    supplier_id=supplier.id,
                    sender='supplier'
                ).order_by(Message.created_at.desc()).first()
                
                if last_supplier_msg and last_supplier_msg.price_mentioned:
                    supplier.final_price = last_supplier_msg.price_mentioned
                
                # Don't auto-complete, just mark as ready for review
                # User should accept the quote manually
                
            except Exception as e:
                print(f"Error checking supplier {supplier.id}: {e}")
        
        db.session.commit()
        print(f"[{datetime.now()}] Checked {len(completed_suppliers)} completed negotiations")
        
    except Exception as e:
        print(f"Error in check_completed_negotiations: {e}")


def start_negotiation_for_session(session_id: int):
    """
    Manually trigger negotiation processing for a specific session.
    Can be called from API endpoint.
    """
    try:
        session = NegotiationSession.query.get(session_id)
        if not session:
            return {'error': 'Session not found'}
        
        suppliers = Supplier.query.filter_by(session_id=session_id).all()
        requirements = json.loads(session.extracted_requirements) if session.extracted_requirements else {}
        
        processed = 0
        for supplier in suppliers:
            if supplier.status in ['pending', 'negotiating'] and supplier.negotiation_round < 2:
                process_supplier_negotiation(supplier)
                processed += 1
        
        return {
            'session_id': session_id,
            'suppliers_processed': processed,
            'total_suppliers': len(suppliers)
        }
        
    except Exception as e:
        return {'error': str(e)}


def get_scheduler_status():
    """Get current scheduler status"""
    if not scheduler:
        return {'status': 'not_initialized'}
    
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            'id': job.id,
            'name': job.name,
            'next_run': job.next_run_time.isoformat() if job.next_run_time else None,
            'trigger': str(job.trigger)
        })
    
    return {
        'status': 'running' if scheduler.running else 'stopped',
        'jobs': jobs
    }


def shutdown_scheduler():
    """Shutdown the scheduler gracefully"""
    global scheduler
    if scheduler:
        scheduler.shutdown()
        print("Scheduler shut down")

