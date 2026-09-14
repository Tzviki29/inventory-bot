import os
import json
from flask import Flask, request, jsonify, render_template
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ---- Google Sheets connection ----
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SPREADSHEET_ID = "1gU5rS84PH0PFBZPOlobFF4kra8F_I2S0FqNhiasc4_8"

def get_sheet():
    # The credentials JSON is stored as an environment variable on Render
    # (not as a file) so we never have to upload the secret key file anywhere.
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    return sheet


def normalize(text):
    """Makes header matching forgiving of extra spaces/capitalization."""
    return text.strip().upper()


def find_column(header, target_name):
    normalized_header = [normalize(h) for h in header]
    return normalized_header.index(normalize(target_name))


def find_item_row(sheet, item_number):
    """Returns (row_number, row_values) for the given item number, or (None, None)."""
    all_values = sheet.get_all_values()
    header = all_values[0]
    item_col = find_column(header, "ITEM NUMBER")

    for i, row in enumerate(all_values[1:], start=2):  # row 1 is header
        if row[item_col].strip() == str(item_number).strip():
            return i, row, header
    return None, None, header


def get_locations(row, header):
    """Returns a list of every non-empty location value, starting at the
    LOCATION column and continuing through any extra location columns to
    the right of it (D, E, F... K, etc)."""
    loc_start_col = find_column(header, "LOCATION")
    locations = [cell.strip() for cell in row[loc_start_col:] if cell.strip()]
    return locations


def write_locations(sheet, row_num, header, new_locations):
    """Writes a full list of locations back into the LOCATION column(s),
    clearing out any leftover old values in columns that are no longer used."""
    loc_start_col = find_column(header, "LOCATION")
    num_location_slots = len(header) - loc_start_col

    if len(new_locations) > num_location_slots:
        raise ValueError(
            f"Too many locations — the sheet only has room for {num_location_slots}."
        )

    for offset in range(num_location_slots):
        # gspread columns/rows are 1-indexed
        col_num = loc_start_col + offset + 1
        value = new_locations[offset] if offset < len(new_locations) else ""
        sheet.update_cell(row_num, col_num, value)


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

    qty_col = find_column(header, "QUANTITY")
    locations = get_locations(row, header)
    quantity = int(row[qty_col])

    return jsonify({
        "item": item_number,
        "quantity": quantity,
        "out_of_stock": quantity <= 0,
        "location": ", ".join(locations)
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

    qty_col = find_column(header, "QUANTITY")
    locations = get_locations(row, header)

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
        "location": ", ".join(locations),
        "previous_quantity": current_qty,
        "taken": take_amount,
        "new_quantity": new_qty
    })


@app.route("/relocate", methods=["POST"])
def relocate():
    data = request.get_json()
    item_number = str(data.get("item", "")).strip()
    action = data.get("action")  # "replace", "add", or "clear"

    if not item_number or not action:
        return jsonify({"error": "Missing item number or action"}), 400

    sheet = get_sheet()
    row_num, row, header = find_item_row(sheet, item_number)

    if row_num is None:
        return jsonify({"error": f"Item {item_number} not found"}), 404

    current_locations = get_locations(row, header)

    if action == "replace":
        old_location = str(data.get("old_location", "")).strip()
        new_location = str(data.get("new_location", "")).strip()
        if not old_location or not new_location:
            return jsonify({"error": "Missing old or new location"}), 400
        if old_location not in current_locations:
            return jsonify({"error": f"'{old_location}' is not a current location for this item"}), 400
        new_locations = [
            new_location if loc == old_location else loc
            for loc in current_locations
        ]

    elif action == "add":
        new_location = str(data.get("new_location", "")).strip()
        if not new_location:
            return jsonify({"error": "Missing new location"}), 400
        if new_location in current_locations:
            return jsonify({"error": f"Item is already listed at '{new_location}'"}), 400
        new_locations = current_locations + [new_location]

    elif action == "clear":
        new_location = str(data.get("new_location", "")).strip()
        if not new_location:
            return jsonify({"error": "Missing new location"}), 400
        new_locations = [new_location]

    else:
        return jsonify({"error": f"Unknown action '{action}'"}), 400

    try:
        write_locations(sheet, row_num, header, new_locations)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "item": item_number,
        "location": ", ".join(new_locations)
    })


if __name__ == "__main__":
    app.run(debug=True)
