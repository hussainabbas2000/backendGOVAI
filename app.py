import os
import tempfile
import requests
from flask import Flask, request, jsonify, Blueprint
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
import json
import re
from flask_cors import CORS
from quote import quote_bp, init_db
from notifications import notifications_bp, init_notifications_db
from compliance import compliance_bp, init_compliance_db
from pdf_generator import pdf_bp, init_pdf_db
from email_webhook import webhook_bp, init_webhook_db
from background_jobs import init_background_jobs, get_scheduler_status, start_negotiation_for_session

load_dotenv()
app = Flask(__name__)
# Configure database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sam_gov_negotiations.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)

# Initialize the quote blueprint with the database
init_db(db)

# Initialize the notifications blueprint with the database
init_notifications_db(db)

# Initialize the compliance blueprint with the database
init_compliance_db(db)

# Initialize the PDF blueprint with the database
init_pdf_db(db)

# Register the blueprints
app.register_blueprint(quote_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(compliance_bp)
app.register_blueprint(pdf_bp)
app.register_blueprint(webhook_bp)

from quote import NegotiationSession, Supplier, Message
from notifications import Notification

# Initialize webhook after models are available
init_webhook_db(db, Supplier, Message, NegotiationSession)


# Background job control routes
@app.route('/api/jobs/status', methods=['GET'])
def job_status():
    """Get background job scheduler status"""
    status = get_scheduler_status()
    return jsonify(status)


@app.route('/api/jobs/process-session/<int:session_id>', methods=['POST'])
def process_session(session_id):
    """Manually trigger processing for a negotiation session"""
    result = start_negotiation_for_session(session_id)
    return jsonify(result)


CORS(app, resources={
    r"/analyze-solicitations": {
        "origins": [
            "http://localhost:9002",                     # local dev
            r"^http://127\.0\.0\.1(:[0-9]+)?$",
            "https://gov-ai-frontend.vercel.app"           # deployed frontend
        ]
    },
    r"/api/*": {  # This covers all quote and notifications blueprint routes
        "origins": [
            "http://localhost:3000",                      # Next.js local dev
            "http://localhost:9002",                      # your existing local dev
            "https://gov-ai-frontend.vercel.app",         # deployed frontend
            "*"  # You can restrict this in production
        ]
    },
    r"/message-chat": {
        "origins": [
            "http://localhost:3000",
            "http://localhost:9002",
            "https://gov-ai-frontend.vercel.app",
            "*"
        ]
    },
    r"/webhook/*": {  # Email webhook endpoints
        "origins": "*"
    }
})
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Your detailed instruction prompt
SYSTEM_PROMPT = {
    "type": "input_text",
    "text": """
    You are now acting as my full-spectrum government contracting and sourcing expert. You specialize in working across all U.S. government agencies — including civilian, defense, and intelligence sectors — and you're proficient in interpreting solicitations from federal, state, and local governments.

Your job is to assist me through the entire sourcing and procurement process for government contracts. I will provide you with solicitation docs, and you will:

🔍 1. Read and Interpret the Solicitations
Analyze any solicitation documents (RFI, RFQ, RFP, IDIQ, BPA, etc.) and provide a plain-English summary of the requirement.

Identify the agency issuing it and explain the mission or goal of the procurement.

Highlight any critical requirements, special instructions, and mandatory compliance points.

🧠 2. Extract All Key Contract Data
From the solicitation, extract and organize the following:

Solicitation Number

NAICS Code(s)

PSC/FSC Code (if listed)

Set-aside Type (e.g., Small Business, 8(a), SDVOSB)

Contract Type (e.g., FFP, IDIQ, GSA Schedule)

Response Deadline and Submission Method

Evaluation Criteria (technical, price, past performance, etc.)

Product or Service Description (this is must)

Quantity, Units, and Delivery Schedule (this is must)

Contracting Officer or POC Contact Info

Any attachments or referenced FAR clauses

🔎 3. Identify and Understand the Product/Service
Break down exactly what product or service is being requested.

Translate technical language or obscure item descriptions into standard commercial equivalents.

If applicable, explain any relevant specifications, standards (e.g., MIL-SPEC, ANSI, ISO), or compliance certifications required.

🛒 4. Source the Product or Service
Use sourcing logic to locate the exact or equivalent item(s) being requested.

Search GSA Advantage, DLA, SAM.gov vendor lists, and major commercial suppliers (e.g., Grainger, Fastenal, McKesson, Dell, CDW).

Include relevant part numbers, prices, and lead times.

🤝 5. Find Suitable and Compliant Suppliers
Recommend 2–3 legitimate, cost-effective suppliers who meet the government’s requirements.

Prefer vendors that are: U.S.-based, registered in SAM.gov, and classified as small business, 8(a), HUBZone, woman-owned, or veteran-owned (if the set-aside requires it).

Include links to supplier profiles or catalogs and reasons for each recommendation.

💰 6. Price Comparison and Cost Optimization
Compare all pricing options and identify the lowest possible cost that still meets quality and compliance standards.

Include total cost breakdown (unit cost, shipping, tax, etc.)

Suggest whether to quote direct, use a distributor, or purchase via an existing contract vehicle (e.g., GSA Schedule, NASA SEWP, DLA TLSP).

📋 7. Final Recommendations and Next Steps
Provide a summary of findings and a recommended sourcing path.

List next steps for me to take — e.g., gather compliance docs, contact supplier, submit capability statement, or prepare a quote.

If the opportunity is not viable, explain why and suggest alternatives.

📎 Format:
Organize all output using clear section headers, bullet points, tables (if needed), and logical flow. Keep technical language concise but accurate. If the solicitation is missing key info, ask clarifying questions.
    
    return your response in JSON with appropriate fields. Do not use any conversational cues just strictly provide the required information in this json way:
    <json field>: <related output text>
    """
}


def parse_raw_output(raw_output):
    try:
        # Step 1: Decode the outer string (remove escape characters)
        cleaned_str = bytes(raw_output, "utf-8").decode("unicode_escape")
        
        # Step 2: Strip any extra quotes that wrapped the object
        if cleaned_str.startswith('"') and cleaned_str.endswith('"'):
            cleaned_str = cleaned_str[1:-1]

        # Step 3: Now parse the inner JSON string
        result = json.loads(cleaned_str)
        return result
    except Exception as e:
        print("Failed to parse raw_output:", e)
        return None

def download_and_upload_files(urls):
    uploaded_files = []
    for url in urls:
        try:
            response = requests.get(url)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            # Upload to OpenAI - use context manager to properly close file
            with open(tmp_path, "rb") as f:
                upload = openai_client.files.create(
                    file=f,
                    purpose="user_data"
                )

            uploaded_files.append({
                "type": "input_file",
                "file_id": upload.id
            })

            # Now safe to delete - file handle is closed
            try:
                os.unlink(tmp_path)
            except Exception as del_err:
                print(f"Warning: Could not delete temp file {tmp_path}: {del_err}")

        except Exception as e:
            print(f"Error downloading or uploading file from {url}: {e}")
    return uploaded_files


# Prompt for extracting key info from a single document
EXTRACTION_PROMPT = """
Extract the following key information from this document in JSON format:
- solicitation_number
- naics_codes (list)
- psc_fsc_code
- set_aside_type
- contract_type
- response_deadline
- submission_method
- evaluation_criteria
- product_service_description
- quantity_units_delivery
- contracting_officer_contact
- far_clauses (list)
- key_requirements (list of strings)

If any field is not found, use null. Return ONLY valid JSON, no other text.
"""

# Prompt for combining extracted data into final summary
FINAL_SUMMARY_PROMPT = """
Based on the following extracted data from multiple solicitation documents, create a comprehensive analysis following this structure:

**Extracted Data:**
{extracted_data}

**Original System Instructions:**
""" + SYSTEM_PROMPT["text"] + """

Combine all the extracted information into a single cohesive summary. Return your response as valid JSON.
"""


def analyze_single_document(file_input):
    """Analyze a single document and extract key information"""
    try:
        response = openai_client.responses.create(
            model="gpt-4o-mini",  # Use mini for individual doc extraction (cheaper & faster)
            input=[
                {
                    "role": "user",
                    "content": [
                        file_input,
                        {"type": "input_text", "text": EXTRACTION_PROMPT}
                    ]
                }
            ]
        )
        raw_output = response.output_text
        # Try to parse JSON
        try:
            # Clean up the response - remove markdown code blocks if present
            cleaned = raw_output.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            return json.loads(cleaned.strip())
        except:
            return {"raw_text": raw_output}
    except Exception as e:
        print(f"Error analyzing document: {e}")
        return {"error": str(e)}


def create_final_summary(extracted_data_list):
    """Combine extracted data from all documents into final summary"""
    try:
        combined_data = json.dumps(extracted_data_list, indent=2)
        
        response = openai_client.chat.completions.create(
            model="gpt-4o",  # Use full model for final synthesis
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT["text"]
                },
                {
                    "role": "user", 
                    "content": f"Here is the extracted data from the solicitation documents:\n\n{combined_data}\n\nPlease provide the full analysis as per your instructions. Return valid JSON only."
                }
            ],
            max_tokens=4000,
            temperature=0.3
        )
        
        raw_output = response.choices[0].message.content
        # Clean and parse
        cleaned = raw_output.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        return {"raw_output": raw_output}
    except Exception as e:
        print(f"Error creating final summary: {e}")
        return {"error": str(e)}


@app.route("/analyze-solicitations", methods=["POST"])
def analyze_solicitations():
    data = request.get_json()
    urls = data.get("urls")

    if not urls or not isinstance(urls, list):
        return jsonify({"error": "Missing or invalid 'urls' array."}), 400

    print(f"Processing {len(urls)} documents in batches...")
    
    # Upload files to OpenAI
    file_inputs = download_and_upload_files(urls)
    if not file_inputs:
        return jsonify({"error": "Failed to upload any files."}), 500

    # Step 1: Process each document separately to extract key info
    print("Step 1: Extracting key information from each document...")
    extracted_data = []
    for i, file_input in enumerate(file_inputs):
        print(f"  Processing document {i+1}/{len(file_inputs)}...")
        doc_data = analyze_single_document(file_input)
        doc_data["document_index"] = i + 1
        extracted_data.append(doc_data)
    
    print(f"Extracted data from {len(extracted_data)} documents")
    
    # Step 2: Combine all extracted data into final summary
    print("Step 2: Creating final comprehensive summary...")
    try:
        final_summary = create_final_summary(extracted_data)
        return jsonify(final_summary)
    except Exception as e:
        return jsonify({"error": str(e), "extracted_data": extracted_data}), 500


@app.route("/message-chat", methods=["POST"])
def message_chat():
    data = request.get_json()
    
    summary = data.get("summary")
    chat_history = data.get("chatHistory", [])
    user_message = data.get("userMessage")
    
    if not user_message:
        return jsonify({"error": "Missing 'userMessage' field."}), 400
    
    # Build conversation context
    system_message = f"""You are a helpful government contracting assistant. You have access to the following contract summary information:

{json.dumps(summary, indent=2) if summary else "No summary available."}

Use this context to answer questions about the contract. Be helpful, accurate, and concise. If you don't know something or it's not in the provided context, say so."""

    # Build messages array for OpenAI
    messages = [{"role": "system", "content": system_message}]
    
    # Add chat history
    for msg in chat_history:
        role = "assistant" if msg.get("role") == "agent" else "user"
        messages.append({"role": role, "content": msg.get("content", "")})
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=1000,
            temperature=0.7
        )
        
        assistant_message = response.choices[0].message.content
        return jsonify({"message": assistant_message})
    
    except Exception as e:
        print(f"Error in message-chat: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    with app.app_context():
        # Import models first
        from quote import NegotiationSession, Supplier, Message
        db.create_all()
        
        # Initialize background jobs
        init_background_jobs(db, Supplier, Message, NegotiationSession, app.app_context)
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 9000)), debug=True)

