import os
import json
from flask import Flask, request, jsonify, render_template
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ---- Google Sheets connection ----
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SPREADSHEET_ID = "1-zemjSkWCOdOh-Q4Q6_i2QR1sN1p5zd3"

def get_sheet():
    # The credentials JSON is stored as an environment variable on Render
    # (not as a file) so we never have to upload the secret key file anywhere.
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    return sheet


def find_item_row(sheet, item_number):
    """Returns (row_number, row_values) for the given item number, or (None, None)."""
    all_values = sheet.get_all_values()
    header = all_values[0]
    item_col = header.index("ITEM NUMBER")

    for i, row in enumerate(all_values[1:], start=2):  # row 1 is header
        if row[item_col].strip() == str(item_number).strip():
            return i, row, header
    return None, None, header


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/lookup", methods=["GET"])
def lookup():
    item_number = request.args.get("item", "").strip()
    if not item_number:
        return jsonify({"error": "No item number provided"}), 400

    sheet = get_sheet()
    row_num, row, header = find_item_row(sheet, item_number)

    if row_num is None:
        return jsonify({"error": f"Item {item_number} not found"}), 404

    qty_col = header.index("QUANTITY")
    loc_col = header.index("LOCATION")

    return jsonify({
        "item": item_number,
        "quantity": row[qty_col],
        "location": row[loc_col]
    })


@app.route("/take", methods=["POST"])
def take():
    data = request.get_json()
    item_number = str(data.get("item", "")).strip()
    take_amount = data.get("take")

    if not item_number or take_amount is None:
        return jsonify({"error": "Missing item number or amount"}), 400

    try:
        take_amount = int(take_amount)
    except ValueError:
        return jsonify({"error": "Amount taken must be a number"}), 400

    sheet = get_sheet()
    row_num, row, header = find_item_row(sheet, item_number)

    if row_num is None:
        return jsonify({"error": f"Item {item_number} not found"}), 404

    qty_col = header.index("QUANTITY")
    loc_col = header.index("LOCATION")

    current_qty = int(row[qty_col])

    if take_amount > current_qty:
        return jsonify({
            "error": f"Only {current_qty} left of item {item_number} — can't take {take_amount}"
        }), 400

    new_qty = current_qty - take_amount

    # gspread columns are 1-indexed, so add 1
    sheet.update_cell(row_num, qty_col + 1, new_qty)

    return jsonify({
        "item": item_number,
        "location": row[loc_col],
        "previous_quantity": current_qty,
        "taken": take_amount,
        "new_quantity": new_qty
    })


if __name__ == "__main__":
    app.run(debug=True)
