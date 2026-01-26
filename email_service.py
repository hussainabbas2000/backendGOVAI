"""
Email Service Module for RFQ Email Dispatch
Uses SendGrid for sending emails to vendors
"""

import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, Attachment, FileContent, FileName, FileType, Disposition
from dotenv import load_dotenv
import base64
from datetime import datetime

load_dotenv()

# SendGrid configuration
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "procurement@govcontract.com")
FROM_NAME = os.getenv("FROM_NAME", "GovContract Procurement Team")

def get_sendgrid_client():
    """Get SendGrid client instance"""
    if not SENDGRID_API_KEY:
        raise ValueError("SENDGRID_API_KEY environment variable is not set")
    return SendGridAPIClient(SENDGRID_API_KEY)


def send_rfq_email(
    to_email: str,
    vendor_name: str,
    subject: str,
    rfq_content: str,
    opportunity_title: str = None,
    opportunity_id: str = None,
    deadline: str = None,
    attachments: list = None
) -> dict:
    """
    Send RFQ email to a vendor
    
    Args:
        to_email: Vendor email address
        vendor_name: Name of the vendor company
        subject: Email subject line
        rfq_content: The RFQ message content
        opportunity_title: Title of the opportunity
        opportunity_id: ID of the opportunity
        deadline: Response deadline
        attachments: List of attachment dicts with 'content', 'filename', 'type'
    
    Returns:
        dict with 'success', 'message_id', 'error' keys
    """
    try:
        # Build HTML email content
        html_content = _build_rfq_html(
            vendor_name=vendor_name,
            rfq_content=rfq_content,
            opportunity_title=opportunity_title,
            opportunity_id=opportunity_id,
            deadline=deadline
        )
        
        # Create the email
        message = Mail(
            from_email=Email(FROM_EMAIL, FROM_NAME),
            to_emails=To(to_email, vendor_name),
            subject=subject,
            html_content=Content("text/html", html_content)
        )
        
        # Add plain text version
        plain_content = _build_rfq_plain_text(
            vendor_name=vendor_name,
            rfq_content=rfq_content,
            opportunity_title=opportunity_title,
            opportunity_id=opportunity_id,
            deadline=deadline
        )
        message.add_content(Content("text/plain", plain_content))
        
        # Add attachments if provided
        if attachments:
            for att in attachments:
                attachment = Attachment(
                    FileContent(base64.b64encode(att['content']).decode()),
                    FileName(att['filename']),
                    FileType(att.get('type', 'application/pdf')),
                    Disposition('attachment')
                )
                message.add_attachment(attachment)
        
        # Send the email
        sg = get_sendgrid_client()
        response = sg.send(message)
        
        return {
            'success': True,
            'status_code': response.status_code,
            'message_id': response.headers.get('X-Message-Id'),
            'error': None
        }
        
    except Exception as e:
        return {
            'success': False,
            'status_code': None,
            'message_id': None,
            'error': str(e)
        }


def send_negotiation_email(
    to_email: str,
    vendor_name: str,
    subject: str,
    negotiation_content: str,
    round_number: int,
    opportunity_title: str = None
) -> dict:
    """
    Send negotiation round email to a vendor
    
    Args:
        to_email: Vendor email address
        vendor_name: Name of the vendor company
        subject: Email subject line
        negotiation_content: The negotiation message content
        round_number: Current negotiation round (1 or 2)
        opportunity_title: Title of the opportunity
    
    Returns:
        dict with 'success', 'message_id', 'error' keys
    """
    try:
        html_content = _build_negotiation_html(
            vendor_name=vendor_name,
            negotiation_content=negotiation_content,
            round_number=round_number,
            opportunity_title=opportunity_title
        )
        
        message = Mail(
            from_email=Email(FROM_EMAIL, FROM_NAME),
            to_emails=To(to_email, vendor_name),
            subject=subject,
            html_content=Content("text/html", html_content)
        )
        
        sg = get_sendgrid_client()
        response = sg.send(message)
        
        return {
            'success': True,
            'status_code': response.status_code,
            'message_id': response.headers.get('X-Message-Id'),
            'error': None
        }
        
    except Exception as e:
        return {
            'success': False,
            'status_code': None,
            'message_id': None,
            'error': str(e)
        }


def send_notification_email(
    to_email: str,
    user_name: str,
    subject: str,
    notification_type: str,
    content: dict
) -> dict:
    """
    Send notification email to user
    
    Args:
        to_email: User email address
        user_name: Name of the user
        subject: Email subject line
        notification_type: Type of notification (negotiation_complete, bid_update, etc.)
        content: Dict containing notification details
    
    Returns:
        dict with 'success', 'message_id', 'error' keys
    """
    try:
        html_content = _build_notification_html(
            user_name=user_name,
            notification_type=notification_type,
            content=content
        )
        
        message = Mail(
            from_email=Email(FROM_EMAIL, FROM_NAME),
            to_emails=To(to_email, user_name),
            subject=subject,
            html_content=Content("text/html", html_content)
        )
        
        sg = get_sendgrid_client()
        response = sg.send(message)
        
        return {
            'success': True,
            'status_code': response.status_code,
            'message_id': response.headers.get('X-Message-Id'),
            'error': None
        }
        
    except Exception as e:
        return {
            'success': False,
            'status_code': None,
            'message_id': None,
            'error': str(e)
        }


def _build_rfq_html(vendor_name, rfq_content, opportunity_title, opportunity_id, deadline):
    """Build HTML content for RFQ email"""
    deadline_text = f"<p><strong>Response Deadline:</strong> {deadline}</p>" if deadline else ""
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #1a56db; color: white; padding: 20px; text-align: center; }}
            .content {{ padding: 20px; background-color: #f9fafb; }}
            .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #666; }}
            .highlight {{ background-color: #fef3c7; padding: 10px; border-radius: 4px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Request for Quotation</h1>
            </div>
            <div class="content">
                <p>Dear {vendor_name},</p>
                
                {f'<div class="highlight"><strong>Opportunity:</strong> {opportunity_title}<br/><strong>ID:</strong> {opportunity_id}</div>' if opportunity_title else ''}
                
                <div style="white-space: pre-wrap;">{rfq_content}</div>
                
                {deadline_text}
                
                <p>We look forward to your response.</p>
                
                <p>Best regards,<br/>
                {FROM_NAME}</p>
            </div>
            <div class="footer">
                <p>This is an automated message from GovContract Navigator.</p>
                <p>To respond, please reply directly to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """


def _build_rfq_plain_text(vendor_name, rfq_content, opportunity_title, opportunity_id, deadline):
    """Build plain text content for RFQ email"""
    text = f"""
REQUEST FOR QUOTATION

Dear {vendor_name},

"""
    if opportunity_title:
        text += f"Opportunity: {opportunity_title}\n"
    if opportunity_id:
        text += f"ID: {opportunity_id}\n"
    text += "\n"
    
    text += rfq_content
    text += "\n\n"
    
    if deadline:
        text += f"Response Deadline: {deadline}\n\n"
    
    text += f"""
We look forward to your response.

Best regards,
{FROM_NAME}

---
This is an automated message from GovContract Navigator.
To respond, please reply directly to this email.
"""
    return text


def _build_negotiation_html(vendor_name, negotiation_content, round_number, opportunity_title):
    """Build HTML content for negotiation email"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #059669; color: white; padding: 20px; text-align: center; }}
            .content {{ padding: 20px; background-color: #f9fafb; }}
            .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #666; }}
            .round-badge {{ background-color: #dbeafe; color: #1e40af; padding: 5px 10px; border-radius: 4px; display: inline-block; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Negotiation Update</h1>
                <span class="round-badge">Round {round_number}</span>
            </div>
            <div class="content">
                <p>Dear {vendor_name},</p>
                
                {f'<p><strong>Re: {opportunity_title}</strong></p>' if opportunity_title else ''}
                
                <div style="white-space: pre-wrap;">{negotiation_content}</div>
                
                <p>We appreciate your continued engagement.</p>
                
                <p>Best regards,<br/>
                {FROM_NAME}</p>
            </div>
            <div class="footer">
                <p>This is an automated message from GovContract Navigator.</p>
                <p>To respond, please reply directly to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """


def _build_notification_html(user_name, notification_type, content):
    """Build HTML content for notification email"""
    
    if notification_type == 'negotiation_complete':
        body = f"""
        <h2>Negotiation Complete!</h2>
        <p>Great news! The negotiation with <strong>{content.get('vendor_name', 'a vendor')}</strong> has been completed.</p>
        
        <div style="background-color: #ecfdf5; padding: 15px; border-radius: 8px; margin: 15px 0;">
            <h3 style="margin-top: 0;">Final Terms</h3>
            <p><strong>Final Price:</strong> ${content.get('final_price', 'N/A')}</p>
            <p><strong>Delivery Details:</strong> {content.get('delivery_details', 'As discussed')}</p>
            {f"<p><strong>Notes:</strong> {content.get('notes', '')}</p>" if content.get('notes') else ''}
        </div>
        
        <p>You can review the full negotiation history and proceed with bid submission in your dashboard.</p>
        """
    elif notification_type == 'bid_update':
        body = f"""
        <h2>Bid Status Update</h2>
        <p>Your bid <strong>{content.get('bid_title', 'Untitled')}</strong> has been updated.</p>
        
        <div style="background-color: #dbeafe; padding: 15px; border-radius: 8px; margin: 15px 0;">
            <p><strong>New Status:</strong> {content.get('status', 'Updated')}</p>
            {f"<p><strong>Details:</strong> {content.get('details', '')}</p>" if content.get('details') else ''}
        </div>
        """
    else:
        body = f"""
        <h2>Notification</h2>
        <p>{content.get('message', 'You have a new notification.')}</p>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #7c3aed; color: white; padding: 20px; text-align: center; }}
            .content {{ padding: 20px; background-color: #f9fafb; }}
            .footer {{ padding: 20px; text-align: center; font-size: 12px; color: #666; }}
            .button {{ background-color: #1a56db; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; display: inline-block; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>GovContract Navigator</h1>
            </div>
            <div class="content">
                <p>Hello {user_name},</p>
                
                {body}
                
                <p style="text-align: center; margin-top: 30px;">
                    <a href="https://sam-gov-liard.vercel.app" class="button">View in Dashboard</a>
                </p>
            </div>
            <div class="footer">
                <p>This is an automated notification from GovContract Navigator.</p>
            </div>
        </div>
    </body>
    </html>
    """

