"""
PDF Generator Module
Creates bid package PDFs for government contract submissions
"""

from flask import Blueprint, request, jsonify, send_file
from datetime import datetime
import os
import json
import io
from dotenv import load_dotenv

load_dotenv()

# Create Blueprint
pdf_bp = Blueprint('pdf', __name__, url_prefix='/api/pdf')

# Database reference (set by init function)
db = None


def init_pdf_db(database):
    """Initialize database from main app"""
    global db
    db = database


def generate_bid_package_html(
    opportunity_data: dict,
    selected_vendor: dict,
    compliance_data: dict = None,
    negotiation_summary: dict = None,
    user_info: dict = None
) -> str:
    """
    Generate HTML content for bid package PDF
    
    Args:
        opportunity_data: Contract opportunity details
        selected_vendor: Selected vendor information with pricing
        compliance_data: Compliance verification results
        negotiation_summary: Summary of negotiation rounds
        user_info: Bidder company information
    
    Returns:
        HTML string for PDF generation
    """
    
    # Default values
    company_name = user_info.get('company_name', 'GovContract Solutions') if user_info else 'GovContract Solutions'
    company_address = user_info.get('address', '123 Business Ave, Suite 100') if user_info else '123 Business Ave, Suite 100'
    company_phone = user_info.get('phone', '(555) 123-4567') if user_info else '(555) 123-4567'
    company_email = user_info.get('email', 'bids@company.com') if user_info else 'bids@company.com'
    
    current_date = datetime.now().strftime('%B %d, %Y')
    
    # Calculate pricing details
    vendor_price = selected_vendor.get('final_price') or selected_vendor.get('current_price') or 0
    
    # Build compliance section
    compliance_html = ""
    if compliance_data:
        checks_html = ""
        for check in compliance_data.get('checks', []):
            status_color = '#059669' if check['status'] == 'passed' else ('#f59e0b' if check['status'] == 'warning' else '#dc2626')
            status_icon = '✓' if check['status'] == 'passed' else ('⚠' if check['status'] == 'warning' else '✗')
            checks_html += f"""
            <tr>
                <td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">{check['name']}</td>
                <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; color: {status_color}; font-weight: bold;">{status_icon} {check['status'].upper()}</td>
                <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; font-size: 12px;">{check['details']}</td>
            </tr>
            """
        
        compliance_html = f"""
        <div class="section">
            <h2>5. Compliance Verification</h2>
            <p><strong>Compliance Score:</strong> {compliance_data.get('compliance_score', 'N/A')}%</p>
            <p><strong>Overall Status:</strong> {compliance_data.get('overall_status', 'N/A').replace('_', ' ').title()}</p>
            <table style="width: 100%; border-collapse: collapse; margin-top: 10px;">
                <thead>
                    <tr style="background-color: #f3f4f6;">
                        <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d1d5db;">Check</th>
                        <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d1d5db;">Status</th>
                        <th style="padding: 8px; text-align: left; border-bottom: 2px solid #d1d5db;">Details</th>
                    </tr>
                </thead>
                <tbody>
                    {checks_html}
                </tbody>
            </table>
        </div>
        """
    
    # Build negotiation history section
    negotiation_html = ""
    if negotiation_summary:
        rounds_html = ""
        for msg in negotiation_summary.get('messages', [])[-6:]:  # Last 6 messages
            sender = "Buyer" if msg.get('sender') == 'buyer' else selected_vendor.get('company_name', 'Vendor')
            price_badge = f"<span style='background-color: #dbeafe; padding: 2px 6px; border-radius: 4px; font-size: 11px;'>${msg.get('price_mentioned', 0):,.2f}</span>" if msg.get('price_mentioned') else ""
            rounds_html += f"""
            <div style="margin-bottom: 10px; padding: 10px; background-color: {'#f0fdf4' if msg.get('sender') == 'buyer' else '#f8fafc'}; border-radius: 4px;">
                <strong>{sender}:</strong> {price_badge}
                <p style="margin: 5px 0 0 0; font-size: 12px;">{msg.get('content', '')[:200]}{'...' if len(msg.get('content', '')) > 200 else ''}</p>
            </div>
            """
        
        negotiation_html = f"""
        <div class="section">
            <h2>6. Negotiation Summary</h2>
            <p><strong>Negotiation Rounds:</strong> {negotiation_summary.get('round_count', 'N/A')}</p>
            <p><strong>Initial Quote:</strong> ${negotiation_summary.get('initial_price', 0):,.2f}</p>
            <p><strong>Final Price:</strong> ${negotiation_summary.get('final_price', 0):,.2f}</p>
            <p><strong>Savings Achieved:</strong> ${negotiation_summary.get('savings', 0):,.2f} ({negotiation_summary.get('savings_percent', 0):.1f}%)</p>
            <h3>Communication History</h3>
            {rounds_html}
        </div>
        """
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Bid Package - {opportunity_data.get('title', 'Government Contract')}</title>
        <style>
            @page {{
                size: letter;
                margin: 1in;
            }}
            body {{
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 11pt;
                line-height: 1.5;
                color: #1f2937;
            }}
            .header {{
                text-align: center;
                border-bottom: 3px solid #1a56db;
                padding-bottom: 20px;
                margin-bottom: 30px;
            }}
            .header h1 {{
                color: #1a56db;
                margin: 0;
                font-size: 24pt;
            }}
            .header .subtitle {{
                color: #6b7280;
                font-size: 12pt;
                margin-top: 5px;
            }}
            .section {{
                margin-bottom: 25px;
                page-break-inside: avoid;
            }}
            .section h2 {{
                color: #1a56db;
                border-bottom: 1px solid #e5e7eb;
                padding-bottom: 5px;
                font-size: 14pt;
            }}
            .section h3 {{
                color: #374151;
                font-size: 12pt;
                margin-top: 15px;
            }}
            .info-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 10px;
            }}
            .info-item {{
                margin-bottom: 8px;
            }}
            .info-item label {{
                color: #6b7280;
                font-size: 10pt;
            }}
            .info-item value {{
                display: block;
                font-weight: 500;
            }}
            .price-box {{
                background-color: #f0fdf4;
                border: 2px solid #059669;
                border-radius: 8px;
                padding: 15px;
                margin: 15px 0;
            }}
            .price-box h3 {{
                color: #059669;
                margin: 0 0 10px 0;
            }}
            .price-large {{
                font-size: 28pt;
                font-weight: bold;
                color: #059669;
            }}
            .footer {{
                margin-top: 40px;
                padding-top: 20px;
                border-top: 1px solid #e5e7eb;
                font-size: 10pt;
                color: #6b7280;
            }}
            .signature-section {{
                margin-top: 40px;
                page-break-inside: avoid;
            }}
            .signature-line {{
                border-bottom: 1px solid #1f2937;
                width: 250px;
                margin-top: 50px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>BID PACKAGE</h1>
            <div class="subtitle">Government Contract Submission</div>
            <div class="subtitle">Generated: {current_date}</div>
        </div>
        
        <div class="section">
            <h2>1. Bidder Information</h2>
            <div class="info-grid">
                <div class="info-item">
                    <label>Company Name</label>
                    <value>{company_name}</value>
                </div>
                <div class="info-item">
                    <label>Address</label>
                    <value>{company_address}</value>
                </div>
                <div class="info-item">
                    <label>Phone</label>
                    <value>{company_phone}</value>
                </div>
                <div class="info-item">
                    <label>Email</label>
                    <value>{company_email}</value>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h2>2. Contract Opportunity Details</h2>
            <div class="info-item">
                <label>Opportunity Title</label>
                <value>{opportunity_data.get('title', 'N/A')}</value>
            </div>
            <div class="info-grid">
                <div class="info-item">
                    <label>Solicitation ID</label>
                    <value>{opportunity_data.get('id', 'N/A')}</value>
                </div>
                <div class="info-item">
                    <label>Agency</label>
                    <value>{opportunity_data.get('agency', opportunity_data.get('department', 'N/A'))}</value>
                </div>
                <div class="info-item">
                    <label>NAICS Code</label>
                    <value>{opportunity_data.get('ncode', opportunity_data.get('naics_code', 'N/A'))}</value>
                </div>
                <div class="info-item">
                    <label>Type</label>
                    <value>{opportunity_data.get('type', 'N/A')}</value>
                </div>
                <div class="info-item">
                    <label>Closing Date</label>
                    <value>{opportunity_data.get('closingDate', opportunity_data.get('deadline', 'N/A'))}</value>
                </div>
                <div class="info-item">
                    <label>Set-Aside</label>
                    <value>{opportunity_data.get('set_aside_type', 'Full and Open')}</value>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h2>3. Selected Vendor</h2>
            <div class="info-grid">
                <div class="info-item">
                    <label>Vendor Name</label>
                    <value>{selected_vendor.get('company_name', 'N/A')}</value>
                </div>
                <div class="info-item">
                    <label>Contact Email</label>
                    <value>{selected_vendor.get('email', 'N/A')}</value>
                </div>
                <div class="info-item">
                    <label>Negotiation Status</label>
                    <value>{selected_vendor.get('status', 'N/A').replace('_', ' ').title()}</value>
                </div>
                <div class="info-item">
                    <label>Vendor Type</label>
                    <value>{'Manually Added' if selected_vendor.get('is_manual') else 'AI Suggested'}</value>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h2>4. Pricing Summary</h2>
            <div class="price-box">
                <h3>Final Negotiated Price</h3>
                <div class="price-large">${vendor_price:,.2f}</div>
                <p style="margin: 10px 0 0 0; color: #374151;">
                    This price reflects the final negotiated amount with {selected_vendor.get('company_name', 'the selected vendor')}.
                </p>
            </div>
        </div>
        
        {compliance_html}
        
        {negotiation_html}
        
        <div class="signature-section">
            <h2>Authorization</h2>
            <p>I certify that the information provided in this bid package is accurate and complete to the best of my knowledge.</p>
            <div style="display: flex; justify-content: space-between; margin-top: 30px;">
                <div>
                    <div class="signature-line"></div>
                    <p style="margin: 5px 0;">Authorized Signature</p>
                </div>
                <div>
                    <div class="signature-line"></div>
                    <p style="margin: 5px 0;">Date</p>
                </div>
            </div>
            <div style="margin-top: 20px;">
                <div class="signature-line"></div>
                <p style="margin: 5px 0;">Printed Name & Title</p>
            </div>
        </div>
        
        <div class="footer">
            <p>This document was generated by GovContract Navigator on {current_date}.</p>
            <p>Document ID: BID-{opportunity_data.get('id', 'UNKNOWN')[:8]}-{datetime.now().strftime('%Y%m%d%H%M%S')}</p>
        </div>
    </body>
    </html>
    """
    
    return html


def generate_pdf_from_html(html_content: str) -> bytes:
    """
    Generate PDF from HTML content using WeasyPrint
    
    Args:
        html_content: HTML string
    
    Returns:
        PDF bytes
    """
    try:
        from weasyprint import HTML, CSS
        
        # Generate PDF
        pdf_buffer = io.BytesIO()
        HTML(string=html_content).write_pdf(pdf_buffer)
        pdf_buffer.seek(0)
        
        return pdf_buffer.getvalue()
    except ImportError:
        # Fallback if WeasyPrint is not available
        raise ImportError("WeasyPrint is required for PDF generation. Install with: pip install weasyprint")
    except Exception as e:
        raise Exception(f"PDF generation failed: {str(e)}")


# API Routes
@pdf_bp.route('/generate-bid-package', methods=['POST'])
def generate_bid_package():
    """Generate a bid package PDF"""
    
    data = request.json
    opportunity_data = data.get('opportunity', {})
    selected_vendor = data.get('vendor', {})
    compliance_data = data.get('compliance', None)
    negotiation_summary = data.get('negotiation', None)
    user_info = data.get('user_info', None)
    return_html = data.get('return_html', False)  # Option to return HTML instead of PDF
    
    if not opportunity_data or not selected_vendor:
        return jsonify({'error': 'Opportunity and vendor data are required'}), 400
    
    try:
        # Generate HTML
        html_content = generate_bid_package_html(
            opportunity_data=opportunity_data,
            selected_vendor=selected_vendor,
            compliance_data=compliance_data,
            negotiation_summary=negotiation_summary,
            user_info=user_info
        )
        
        if return_html:
            return jsonify({'html': html_content})
        
        # Generate PDF
        pdf_bytes = generate_pdf_from_html(html_content)
        
        # Create filename
        opp_id = opportunity_data.get('id', 'unknown')[:8]
        filename = f"bid_package_{opp_id}_{datetime.now().strftime('%Y%m%d')}.pdf"
        
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        
    except ImportError as e:
        return jsonify({
            'error': str(e),
            'fallback': 'html',
            'html': generate_bid_package_html(
                opportunity_data=opportunity_data,
                selected_vendor=selected_vendor,
                compliance_data=compliance_data,
                negotiation_summary=negotiation_summary,
                user_info=user_info
            )
        }), 200  # Return HTML as fallback
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@pdf_bp.route('/preview-bid-package', methods=['POST'])
def preview_bid_package():
    """Preview bid package as HTML"""
    
    data = request.json
    opportunity_data = data.get('opportunity', {})
    selected_vendor = data.get('vendor', {})
    compliance_data = data.get('compliance', None)
    negotiation_summary = data.get('negotiation', None)
    user_info = data.get('user_info', None)
    
    if not opportunity_data or not selected_vendor:
        return jsonify({'error': 'Opportunity and vendor data are required'}), 400
    
    try:
        html_content = generate_bid_package_html(
            opportunity_data=opportunity_data,
            selected_vendor=selected_vendor,
            compliance_data=compliance_data,
            negotiation_summary=negotiation_summary,
            user_info=user_info
        )
        
        return jsonify({'html': html_content})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

