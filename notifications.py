"""
Notifications Module for In-App Notifications
Handles notification creation, retrieval, and management
"""

from flask import Blueprint, request, jsonify
from datetime import datetime
from email_service import send_notification_email

# Create Blueprint
notifications_bp = Blueprint('notifications', __name__, url_prefix='/api/notifications')

# We'll use the app's db instance
db = None
Notification = None


def init_notifications_db(database):
    """Initialize database from main app"""
    global db, Notification
    db = database
    
    class NotificationModel(db.Model):
        __tablename__ = 'notifications'
        
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.String(100), nullable=True)  # Can be null for system-wide notifications
        title = db.Column(db.String(200), nullable=False)
        message = db.Column(db.Text, nullable=False)
        notification_type = db.Column(db.String(50), nullable=False)  # negotiation_complete, bid_update, vendor_response, etc.
        reference_type = db.Column(db.String(50), nullable=True)  # session, supplier, bid, etc.
        reference_id = db.Column(db.Integer, nullable=True)  # ID of the referenced entity
        is_read = db.Column(db.Boolean, default=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        read_at = db.Column(db.DateTime, nullable=True)
        
        # Additional data stored as JSON string
        extra_data = db.Column(db.Text, nullable=True)
    
    # Make model available globally
    globals()['Notification'] = NotificationModel
    return NotificationModel


def create_notification(
    title: str,
    message: str,
    notification_type: str,
    reference_type: str = None,
    reference_id: int = None,
    user_id: str = None,
    metadata: dict = None,
    send_email_notification: bool = False,
    user_email: str = None,
    user_name: str = None
) -> dict:
    """
    Create a new notification
    
    Args:
        title: Notification title
        message: Notification message content
        notification_type: Type of notification
        reference_type: Type of entity being referenced (session, supplier, bid)
        reference_id: ID of the referenced entity
        user_id: User ID (optional)
        metadata: Additional data as dict
        send_email_notification: Whether to also send email
        user_email: Email for notification (if send_email_notification is True)
        user_name: User name for email greeting
    
    Returns:
        dict with notification data
    """
    import json
    
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        notification_type=notification_type,
        reference_type=reference_type,
        reference_id=reference_id,
        extra_data=json.dumps(metadata) if metadata else None
    )
    
    db.session.add(notification)
    db.session.commit()
    
    # Send email notification if requested
    email_sent = False
    if send_email_notification and user_email:
        try:
            email_content = {
                'message': message,
                **({} if not metadata else metadata)
            }
            
            result = send_notification_email(
                to_email=user_email,
                user_name=user_name or 'User',
                subject=title,
                notification_type=notification_type,
                content=email_content
            )
            email_sent = result.get('success', False)
        except Exception as e:
            print(f"Failed to send notification email: {e}")
    
    return {
        'id': notification.id,
        'title': notification.title,
        'message': notification.message,
        'notification_type': notification.notification_type,
        'reference_type': notification.reference_type,
        'reference_id': notification.reference_id,
        'is_read': notification.is_read,
        'created_at': notification.created_at.isoformat(),
        'email_sent': email_sent
    }


def create_negotiation_complete_notification(
    vendor_name: str,
    opportunity_title: str,
    final_price: float,
    session_id: int,
    supplier_id: int,
    user_id: str = None,
    delivery_details: str = None,
    notes: str = None,
    send_email: bool = True,
    user_email: str = None,
    user_name: str = None
) -> dict:
    """
    Create notification when negotiation is complete
    """
    return create_notification(
        title=f"Negotiation Complete - {vendor_name}",
        message=f"Negotiation with {vendor_name} for '{opportunity_title}' has been completed. Final price: ${final_price:.2f}",
        notification_type='negotiation_complete',
        reference_type='supplier',
        reference_id=supplier_id,
        user_id=user_id,
        metadata={
            'vendor_name': vendor_name,
            'opportunity_title': opportunity_title,
            'final_price': final_price,
            'session_id': session_id,
            'delivery_details': delivery_details,
            'notes': notes
        },
        send_email_notification=send_email,
        user_email=user_email,
        user_name=user_name
    )


def create_vendor_response_notification(
    vendor_name: str,
    opportunity_title: str,
    session_id: int,
    supplier_id: int,
    round_number: int,
    opportunity_id: str = None,
    user_id: str = None
) -> dict:
    """
    Create notification when vendor responds
    """
    return create_notification(
        title=f"Quote Received - {vendor_name}",
        message=f"{vendor_name} has submitted a quote for '{opportunity_title}' (Round {round_number}).",
        notification_type='vendor_response',
        reference_type='supplier',
        reference_id=supplier_id,
        user_id=user_id,
        metadata={
            'vendor_name': vendor_name,
            'opportunity_title': opportunity_title,
            'session_id': session_id,
            'opportunity_id': opportunity_id,
            'round_number': round_number
        }
    )


def create_bid_update_notification(
    bid_title: str,
    new_status: str,
    bid_id: str,
    user_id: str = None,
    details: str = None
) -> dict:
    """
    Create notification for bid status update
    """
    return create_notification(
        title=f"Bid Update - {bid_title}",
        message=f"Your bid '{bid_title}' status has been updated to: {new_status}",
        notification_type='bid_update',
        reference_type='bid',
        reference_id=None,  # Bid IDs are strings
        user_id=user_id,
        metadata={
            'bid_title': bid_title,
            'status': new_status,
            'bid_id': bid_id,
            'details': details
        }
    )


# API Routes
@notifications_bp.route('', methods=['GET'])
def get_notifications():
    """Get all notifications, optionally filtered by user_id and read status"""
    user_id = request.args.get('user_id')
    unread_only = request.args.get('unread_only', 'false').lower() == 'true'
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    query = Notification.query
    
    if user_id:
        query = query.filter(
            (Notification.user_id == user_id) | (Notification.user_id == None)
        )
    
    if unread_only:
        query = query.filter(Notification.is_read == False)
    
    notifications = query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()
    
    # Get unread count
    unread_count = Notification.query.filter(Notification.is_read == False)
    if user_id:
        unread_count = unread_count.filter(
            (Notification.user_id == user_id) | (Notification.user_id == None)
        )
    unread_count = unread_count.count()
    
    return jsonify({
        'notifications': [{
            'id': n.id,
            'title': n.title,
            'message': n.message,
            'notification_type': n.notification_type,
            'reference_type': n.reference_type,
            'reference_id': n.reference_id,
            'is_read': n.is_read,
            'created_at': n.created_at.isoformat(),
            'read_at': n.read_at.isoformat() if n.read_at else None,
            'extra_data': n.extra_data
        } for n in notifications],
        'unread_count': unread_count
    })


@notifications_bp.route('/unread-count', methods=['GET'])
def get_unread_count():
    """Get count of unread notifications"""
    user_id = request.args.get('user_id')
    
    query = Notification.query.filter(Notification.is_read == False)
    
    if user_id:
        query = query.filter(
            (Notification.user_id == user_id) | (Notification.user_id == None)
        )
    
    count = query.count()
    
    return jsonify({'unread_count': count})


@notifications_bp.route('/mark-read', methods=['POST'])
def mark_notifications_read():
    """Mark notifications as read"""
    data = request.json
    notification_ids = data.get('notification_ids', [])
    mark_all = data.get('mark_all', False)
    user_id = data.get('user_id')
    
    if mark_all:
        query = Notification.query.filter(Notification.is_read == False)
        if user_id:
            query = query.filter(
                (Notification.user_id == user_id) | (Notification.user_id == None)
            )
        notifications = query.all()
    elif notification_ids:
        notifications = Notification.query.filter(Notification.id.in_(notification_ids)).all()
    else:
        return jsonify({'error': 'No notifications specified'}), 400
    
    for notification in notifications:
        notification.is_read = True
        notification.read_at = datetime.utcnow()
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'marked_count': len(notifications)
    })


@notifications_bp.route('/<int:notification_id>', methods=['GET'])
def get_notification(notification_id):
    """Get a single notification by ID"""
    notification = Notification.query.get_or_404(notification_id)
    
    return jsonify({
        'id': notification.id,
        'title': notification.title,
        'message': notification.message,
        'notification_type': notification.notification_type,
        'reference_type': notification.reference_type,
        'reference_id': notification.reference_id,
        'is_read': notification.is_read,
        'created_at': notification.created_at.isoformat(),
        'read_at': notification.read_at.isoformat() if notification.read_at else None,
        'extra_data': notification.extra_data
    })


@notifications_bp.route('/<int:notification_id>/mark-read', methods=['POST'])
def mark_single_notification_read(notification_id):
    """Mark a single notification as read"""
    notification = Notification.query.get_or_404(notification_id)
    
    notification.is_read = True
    notification.read_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({'success': True})


@notifications_bp.route('/<int:notification_id>', methods=['DELETE'])
def delete_notification(notification_id):
    """Delete a notification"""
    notification = Notification.query.get_or_404(notification_id)
    
    db.session.delete(notification)
    db.session.commit()
    
    return jsonify({'success': True})


@notifications_bp.route('/test', methods=['POST'])
def create_test_notification():
    """Create a test notification (for development)"""
    data = request.json or {}
    
    result = create_notification(
        title=data.get('title', 'Test Notification'),
        message=data.get('message', 'This is a test notification.'),
        notification_type=data.get('type', 'test'),
        user_id=data.get('user_id')
    )
    
    return jsonify(result)

