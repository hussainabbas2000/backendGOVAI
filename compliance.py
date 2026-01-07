"""
Compliance Verification Module
Checks vendor compliance with government contract requirements
"""

from flask import Blueprint, request, jsonify
from openai import OpenAI
import os
import json
from dotenv import load_dotenv

load_dotenv()

# Create Blueprint
compliance_bp = Blueprint('compliance', __name__, url_prefix='/api/compliance')

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Database reference (set by init function)
db = None


def init_compliance_db(database):
    """Initialize database from main app"""
    global db
    db = database


def verify_vendor_compliance(
    vendor_name: str,
    vendor_email: str,
    opportunity_requirements: dict,
    vendor_certifications: list = None,
    is_manual_vendor: bool = False
) -> dict:
    """
    Verify if a vendor meets compliance requirements for a contract
    
    Args:
        vendor_name: Name of the vendor
        vendor_email: Vendor email
        opportunity_requirements: Dict containing contract requirements
        vendor_certifications: List of vendor's certifications (if known)
        is_manual_vendor: Whether this is a manually added vendor
    
    Returns:
        dict with compliance status and details
    """
    
    compliance_results = {
        'vendor_name': vendor_name,
        'overall_status': 'pending_verification',
        'checks': [],
        'warnings': [],
        'recommendations': []
    }
    
    # Extract requirements from opportunity
    set_aside_type = opportunity_requirements.get('set_aside_type', '')
    required_certs = opportunity_requirements.get('certifications_needed', [])
    naics_code = opportunity_requirements.get('naics_code', '')
    delivery_requirements = opportunity_requirements.get('delivery_requirements', '')
    timeline = opportunity_requirements.get('timeline', '')
    
    # Check 1: Set-aside compliance
    set_aside_check = check_set_aside_compliance(set_aside_type, vendor_name, vendor_certifications)
    compliance_results['checks'].append(set_aside_check)
    
    # Check 2: Certification requirements
    cert_check = check_certifications(required_certs, vendor_certifications)
    compliance_results['checks'].append(cert_check)
    
    # Check 3: SAM.gov registration (simulated)
    sam_check = check_sam_registration(vendor_name, vendor_email)
    compliance_results['checks'].append(sam_check)
    
    # Check 4: NAICS code compatibility
    naics_check = check_naics_compatibility(naics_code, vendor_name)
    compliance_results['checks'].append(naics_check)
    
    # Check 5: Delivery capability
    delivery_check = check_delivery_capability(delivery_requirements, timeline)
    compliance_results['checks'].append(delivery_check)
    
    # Add warnings for manual vendors
    if is_manual_vendor:
        compliance_results['warnings'].append({
            'type': 'manual_vendor',
            'message': 'This vendor was manually added. Verify all compliance requirements independently.'
        })
    
    # Calculate overall status
    failed_checks = [c for c in compliance_results['checks'] if c['status'] == 'failed']
    warning_checks = [c for c in compliance_results['checks'] if c['status'] == 'warning']
    
    if failed_checks:
        compliance_results['overall_status'] = 'non_compliant'
        compliance_results['recommendations'].append(
            f"Address {len(failed_checks)} failed compliance check(s) before proceeding."
        )
    elif warning_checks:
        compliance_results['overall_status'] = 'needs_review'
        compliance_results['recommendations'].append(
            f"Review {len(warning_checks)} item(s) that require verification."
        )
    else:
        compliance_results['overall_status'] = 'compliant'
    
    # Calculate compliance score
    total_checks = len(compliance_results['checks'])
    passed_checks = len([c for c in compliance_results['checks'] if c['status'] == 'passed'])
    compliance_results['compliance_score'] = round((passed_checks / total_checks) * 100) if total_checks > 0 else 0
    
    return compliance_results


def check_set_aside_compliance(set_aside_type: str, vendor_name: str, vendor_certs: list = None) -> dict:
    """Check if vendor meets set-aside requirements"""
    
    check = {
        'name': 'Set-Aside Compliance',
        'requirement': set_aside_type or 'None specified',
        'status': 'passed',
        'details': ''
    }
    
    if not set_aside_type or set_aside_type.lower() in ['none', 'full and open', 'unrestricted']:
        check['details'] = 'No set-aside restrictions for this opportunity.'
        return check
    
    # Define set-aside types and required certifications
    set_aside_map = {
        'small business': ['Small Business', 'SBA Certified'],
        '8(a)': ['8(a)', 'SBA 8(a)'],
        'hubzone': ['HUBZone', 'HUBZone Certified'],
        'sdvosb': ['SDVOSB', 'Service-Disabled Veteran-Owned'],
        'vosb': ['VOSB', 'Veteran-Owned'],
        'wosb': ['WOSB', 'Women-Owned'],
        'edwosb': ['EDWOSB', 'Economically Disadvantaged WOSB']
    }
    
    set_aside_lower = set_aside_type.lower()
    required_cert = None
    
    for key, certs in set_aside_map.items():
        if key in set_aside_lower:
            required_cert = certs
            break
    
    if required_cert:
        if vendor_certs:
            has_cert = any(
                any(rc.lower() in vc.lower() for rc in required_cert) 
                for vc in vendor_certs
            )
            if has_cert:
                check['details'] = f'Vendor has required {set_aside_type} certification.'
            else:
                check['status'] = 'warning'
                check['details'] = f'Unable to verify {set_aside_type} certification. Manual verification required.'
        else:
            check['status'] = 'warning'
            check['details'] = f'Set-aside type is {set_aside_type}. Verify vendor meets requirements.'
    else:
        check['status'] = 'warning'
        check['details'] = f'Unknown set-aside type: {set_aside_type}. Manual verification recommended.'
    
    return check


def check_certifications(required_certs: list, vendor_certs: list = None) -> dict:
    """Check if vendor has required certifications"""
    
    check = {
        'name': 'Required Certifications',
        'requirement': ', '.join(required_certs) if required_certs else 'None specified',
        'status': 'passed',
        'details': ''
    }
    
    if not required_certs or required_certs == ['As required']:
        check['details'] = 'No specific certifications required or to be verified during contracting.'
        return check
    
    if not vendor_certs:
        check['status'] = 'warning'
        check['details'] = f'Required certifications: {", ".join(required_certs)}. Vendor certifications not verified.'
        return check
    
    missing_certs = []
    for req_cert in required_certs:
        found = any(req_cert.lower() in vc.lower() for vc in vendor_certs)
        if not found:
            missing_certs.append(req_cert)
    
    if missing_certs:
        check['status'] = 'warning'
        check['details'] = f'Unable to verify: {", ".join(missing_certs)}. Manual verification required.'
    else:
        check['details'] = 'All required certifications verified.'
    
    return check


def check_sam_registration(vendor_name: str, vendor_email: str) -> dict:
    """Check SAM.gov registration status (simulated)"""
    
    check = {
        'name': 'SAM.gov Registration',
        'requirement': 'Active registration required',
        'status': 'warning',
        'details': ''
    }
    
    # In a real implementation, this would call SAM.gov API
    # For now, we'll simulate the check
    
    if vendor_email and '@' in vendor_email:
        # Basic check - if email looks legitimate
        domain = vendor_email.split('@')[1].lower()
        suspicious_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
        
        if domain in suspicious_domains:
            check['details'] = 'Vendor uses personal email domain. Verify SAM.gov registration independently.'
        else:
            check['status'] = 'passed'
            check['details'] = 'Business email detected. Recommend verifying SAM.gov registration.'
    else:
        check['details'] = 'No email provided. SAM.gov registration status unknown.'
    
    return check


def check_naics_compatibility(naics_code: str, vendor_name: str) -> dict:
    """Check if vendor industry matches NAICS code"""
    
    check = {
        'name': 'NAICS Code Compatibility',
        'requirement': naics_code or 'None specified',
        'status': 'passed',
        'details': ''
    }
    
    if not naics_code:
        check['details'] = 'No NAICS code specified for this opportunity.'
        return check
    
    # In a real implementation, this would verify against SAM.gov or other databases
    check['status'] = 'warning'
    check['details'] = f'NAICS code {naics_code} required. Verify vendor is registered under this code.'
    
    return check


def check_delivery_capability(delivery_requirements: str, timeline: str) -> dict:
    """Check delivery requirements and timeline feasibility"""
    
    check = {
        'name': 'Delivery Capability',
        'requirement': f'{delivery_requirements or "As specified"} | Timeline: {timeline or "As per contract"}',
        'status': 'passed',
        'details': ''
    }
    
    if not delivery_requirements and not timeline:
        check['details'] = 'Delivery requirements to be confirmed during contracting.'
        return check
    
    # Basic timeline check
    if timeline:
        timeline_lower = timeline.lower()
        if any(word in timeline_lower for word in ['immediate', 'urgent', 'asap', '24 hour', '48 hour']):
            check['status'] = 'warning'
            check['details'] = 'Tight delivery timeline. Confirm vendor can meet requirements.'
        else:
            check['details'] = 'Standard delivery timeline. Confirm during vendor selection.'
    else:
        check['details'] = 'Delivery capability to be verified with vendor.'
    
    return check


def generate_compliance_summary_with_ai(compliance_results: dict, opportunity_data: dict) -> dict:
    """Use AI to generate a comprehensive compliance summary"""
    
    try:
        checks_summary = "\n".join([
            f"- {c['name']}: {c['status'].upper()} - {c['details']}"
            for c in compliance_results.get('checks', [])
        ])
        
        prompt = f"""
        Analyze the following vendor compliance check results and provide a summary recommendation.
        
        Vendor: {compliance_results.get('vendor_name', 'Unknown')}
        Overall Status: {compliance_results.get('overall_status', 'Unknown')}
        Compliance Score: {compliance_results.get('compliance_score', 0)}%
        
        Check Results:
        {checks_summary}
        
        Warnings: {', '.join([w['message'] for w in compliance_results.get('warnings', [])])}
        
        Opportunity: {opportunity_data.get('title', 'Government Contract')}
        
        Provide:
        1. A brief executive summary (2-3 sentences)
        2. Key risks to consider
        3. Recommended actions before proceeding
        
        Return as JSON with keys: executive_summary, key_risks, recommended_actions
        """
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a government contracting compliance expert. Provide clear, actionable compliance assessments."},
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
        
        ai_summary = json.loads(content.strip())
        return ai_summary
        
    except Exception as e:
        return {
            'executive_summary': f"Compliance score: {compliance_results.get('compliance_score', 0)}%. Review individual check results for details.",
            'key_risks': ['Unable to generate AI summary'],
            'recommended_actions': ['Review all compliance checks manually'],
            'error': str(e)
        }


# API Routes
@compliance_bp.route('/verify', methods=['POST'])
def verify_compliance():
    """Verify vendor compliance for an opportunity"""
    
    data = request.json
    vendor_name = data.get('vendor_name')
    vendor_email = data.get('vendor_email', '')
    opportunity_requirements = data.get('requirements', {})
    vendor_certifications = data.get('certifications', [])
    is_manual = data.get('is_manual', False)
    include_ai_summary = data.get('include_ai_summary', True)
    
    if not vendor_name:
        return jsonify({'error': 'Vendor name is required'}), 400
    
    # Run compliance checks
    results = verify_vendor_compliance(
        vendor_name=vendor_name,
        vendor_email=vendor_email,
        opportunity_requirements=opportunity_requirements,
        vendor_certifications=vendor_certifications,
        is_manual_vendor=is_manual
    )
    
    # Add AI summary if requested
    if include_ai_summary:
        ai_summary = generate_compliance_summary_with_ai(
            results,
            {'title': opportunity_requirements.get('opportunity_title', 'Government Contract')}
        )
        results['ai_summary'] = ai_summary
    
    return jsonify(results)


@compliance_bp.route('/batch-verify', methods=['POST'])
def batch_verify_compliance():
    """Verify compliance for multiple vendors"""
    
    data = request.json
    vendors = data.get('vendors', [])
    opportunity_requirements = data.get('requirements', {})
    
    if not vendors:
        return jsonify({'error': 'No vendors provided'}), 400
    
    results = []
    for vendor in vendors:
        result = verify_vendor_compliance(
            vendor_name=vendor.get('name', 'Unknown'),
            vendor_email=vendor.get('email', ''),
            opportunity_requirements=opportunity_requirements,
            vendor_certifications=vendor.get('certifications', []),
            is_manual_vendor=vendor.get('is_manual', False)
        )
        results.append(result)
    
    # Calculate summary statistics
    compliant_count = len([r for r in results if r['overall_status'] == 'compliant'])
    review_count = len([r for r in results if r['overall_status'] == 'needs_review'])
    non_compliant_count = len([r for r in results if r['overall_status'] == 'non_compliant'])
    
    return jsonify({
        'vendors': results,
        'summary': {
            'total': len(results),
            'compliant': compliant_count,
            'needs_review': review_count,
            'non_compliant': non_compliant_count,
            'average_score': round(sum(r['compliance_score'] for r in results) / len(results)) if results else 0
        }
    })

