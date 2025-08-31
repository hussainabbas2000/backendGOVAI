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

load_dotenv()
app = Flask(__name__)
# Configure database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sam_gov_negotiations.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)

# Initialize the quote blueprint with the database
init_db(db)

# Register the blueprint
app.register_blueprint(quote_bp)
from quote import NegotiationSession, Supplier, Message


CORS(app, resources={
    r"/analyze-solicitations": {
        "origins": [
            "http://localhost:9002",                     # local dev
            "https://gov-ai-frontend.vercel.app"           # deployed frontend
        ]
    },
    r"/api/*": {  # This covers all quote blueprint routes
        "origins": [
            "http://localhost:3000",                      # Next.js local dev
            "http://localhost:9002",                      # your existing local dev
            "https://gov-ai-frontend.vercel.app",         # deployed frontend
            "*"  # You can restrict this in production
        ]
    }
})
openai_client = OpenAI(api_key="")

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

            # Upload to OpenAI
            upload = openai_client.files.create(
                file=open(tmp_path, "rb"),
                purpose="user_data"
            )

            uploaded_files.append({
                "type": "input_file",
                "file_id": upload.id
            })

            os.unlink(tmp_path)

        except Exception as e:
            print(f"Error downloading or uploading file from {url}: {e}")
    return uploaded_files

@app.route("/analyze-solicitations", methods=["POST"])
def analyze_solicitations():
    data = request.get_json()
    urls = data.get("urls")

    if not urls or not isinstance(urls, list):
        return jsonify({"error": "Missing or invalid 'urls' array."}), 400

    # Upload files to OpenAI
    file_inputs = download_and_upload_files(urls)
    if not file_inputs:
        return jsonify({"error": "Failed to upload any files."}), 500

    # Add system prompt
    input_payload = file_inputs + [SYSTEM_PROMPT]

    try:
        response = openai_client.responses.create(
            model="gpt-4.1",  # Or "gpt-4o" if supported
            input=[
                {
                    "role": "user",
                    "content": input_payload
                }
            ]
        )

        raw_output = response.output_text

        
        
        try:
            parsed_data = parse_raw_output(raw_output)
            return parsed_data
        except json.JSONDecodeError as e:
            return jsonify({
                "error": "Failed to parse JSON",
                "details": str(e),
                "raw_output": raw_output
            }), 502

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    with app.app_context():
        # Import models first
        from quote import NegotiationSession, Supplier, Message
        db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 9000)), debug=True)

