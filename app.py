import datetime
import os
from flask import Flask, request, jsonify, render_template, send_file
import asyncio
import aiohttp
import csv
import time

app = Flask(__name__)

# --------------------------------------------------------------------
# BLACKLIST
# --------------------------------------------------------------------
BLACKLIST_APTOS = {
    "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
}

BLACKLIST_SOLANA = {
    "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "7eRK1uDfSci41sGF8UbFasuS3KmCeytdeZRQTHhkngkH",
    "5PAhQiYdLBd6SVdjzBQDxUAEFyDdF5ExNPQfcscnPRj5",
    "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w",
    "3UCiejo3mb2JfNaMuf64XxoPPr1LTZEUxNgUBxEgsCHg"
}

# --------------------------------------------------------------------
# RPC ve Token Bilgileri
# --------------------------------------------------------------------
SOLANA_RPC_URL = "https://neat-icy-thunder.solana-mainnet.quiknode.pro/c60b20047057aed971dacf6f8e391ebd7872805a"
SOLANA_TOKEN_ADDRESS = "7yZFFUhq9ac7DY4WobLL539pJEUbMnQ5AGQQuuEMpump"
OSOL_TOKEN_ADDRESS = "2otVNpcHXn9MKeDk3Zby5uanF3s7tki4toaJ3PZcXaUd"

APTOS_API_URL_TEMPLATE = "https://api.aptoscan.com/v1/coins/0x8512b34017e087c3707748869ddc317d83f3fe70ab3a162abdc055c761ca9906::OBOT::OBOT/holders?cluster=mainnet&page={}"
APTOS_TOKEN_DECIMALS = 8

CSV_FILE_PATH = "holders_combined.csv"
CSV_FILE_PATH_OLD = "holders_combined_old.csv"

# --------------------------------------------------------------------
# SOLANA Holder Verilerini Çek
# --------------------------------------------------------------------
async def fetch_solana_holders(rpc_url, token_address):
    headers = {"Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getProgramAccounts",
        "params": [
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            {
                "encoding": "jsonParsed",
                "filters": [
                    {"dataSize": 165},
                    {"memcmp": {"offset": 0, "bytes": token_address}}
                ]
            }
        ]
    }
    solana_holders = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(rpc_url, headers=headers, json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    accounts = result.get("result", [])
                    
                    for acc in accounts:
                        ui_amount = acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"]
                        owner = acc["account"]["data"]["parsed"]["info"]["owner"]

                        if ui_amount > 0 and owner not in BLACKLIST_SOLANA:
                            solana_holders.append({
                                "address": owner,
                                "balance": round(float(ui_amount), 6),
                                "network": "Solana"
                            })
    except Exception as e:
        print(f"Error fetching Solana holders: {e}")
    return solana_holders

# --------------------------------------------------------------------
# APTOS Tek Sayfa Çek
# --------------------------------------------------------------------
async def fetch_aptos_page(session, url):
    try:
        async with session.get(url) as response:
            if response.status == 200:
                result = await response.json()
                if result.get("success") and "coin_holders_list" in result["data"]:
                    return result["data"]["coin_holders_list"]
    except Exception as e:
        print(f"Error fetching Aptos page: {e}")
    return []

# --------------------------------------------------------------------
# APTOS Holder Verilerini Paralel Çek
# --------------------------------------------------------------------
async def fetch_aptos_holders(api_url_template):
    all_holders = []
    async with aiohttp.ClientSession() as session:
        first_page_url = api_url_template.format(1)
        print("Fetching the first page from Aptos API...")
        first_page = await fetch_aptos_page(session, first_page_url)

        if not first_page:
            print("No holders found on the first page.")
            return []

        all_holders.extend(first_page)
        holders_per_page = len(first_page)
        print(f"Holders per page: {holders_per_page}")

        max_pages = 200
        page_urls = [api_url_template.format(page) for page in range(2, max_pages + 1)]
        print(f"Fetching pages 2 to {max_pages} in parallel...")
        tasks = [fetch_aptos_page(session, url) for url in page_urls]
        results = await asyncio.gather(*tasks)

        for idx, page_holders in enumerate(results, start=2):
            if page_holders:
                print(f"Fetched {len(page_holders)} holders from page {idx}.")
                all_holders.extend(page_holders)
            else:
                print(f"Page {idx} returned no data. Stopping fetch.")
                break
    
    # Blacklist filtre
    normalized_holders = []
    for holder in all_holders:
        amount = holder["amount"]
        address = holder["owner_address"]
        if amount > 0 and address not in BLACKLIST_APTOS:
            normalized_holders.append({
                "address": address,
                "balance": round(amount / (10 ** APTOS_TOKEN_DECIMALS), 6),
                "network": "Aptos"
            })
    print(f"Total holders fetched from Aptos: {len(normalized_holders)}")
    return normalized_holders

# --------------------------------------------------------------------
# CSV Sıralama
# --------------------------------------------------------------------
def sort_csv_by_balance(csv_file_path):
    try:
        with open(csv_file_path, mode="r", encoding="utf-8") as csvfile:
            reader = list(csv.reader(csvfile))
            header = reader[0]
            rows = reader[1:]
        
        # Balance sütunu = index 1 => float ile kıyaslama
        sorted_rows = sorted(rows, key=lambda x: float(x[1]), reverse=True)

        with open(csv_file_path, mode="w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(header)
            writer.writerows(sorted_rows)

        print(f"CSV file sorted and saved at: {csv_file_path}")
    except Exception as e:
        print(f"Error sorting CSV file: {e}")

# --------------------------------------------------------------------
# CSV Oluşturma (1 Dakikada Bir Çağrılacak)
# --------------------------------------------------------------------
async def save_holders_to_csv():
    print("Fetching holders data...")
    #aptos_holders = await fetch_aptos_holders(APTOS_API_URL_TEMPLATE)
    solana_holders = await fetch_solana_holders(SOLANA_RPC_URL, SOLANA_TOKEN_ADDRESS)

    combined_holders = solana_holders # + aptos_holders
    print(f"Total holders combined: {len(combined_holders)}")

    with open(CSV_FILE_PATH, mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Address", "Balance", "Network"])
        for holder in combined_holders:
            balance_str = f"{holder['balance']:.6f}"
            writer.writerow([holder["address"], balance_str, holder["network"]])

    sort_csv_by_balance(CSV_FILE_PATH)
    print("CSV updated.")

# --------------------------------------------------------------------
# 2) /check-rank => CSV'den Rank Okuma
# --------------------------------------------------------------------
def get_rank_from_csv(wallet_address, csv_path):
    """
    CSV dosyasını okur, satır satır kontrol ederek 
    cüzdan adresinin rank ve balance değerini döndürür.
    Bulamazsa None döndürür.
    """
    try:
        with open(csv_path, mode="r", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            next(reader)  # başlık satırını atla

            rank = 1
            for row in reader:
                addr, bal, net = row[0], row[1], row[2]
                if addr == wallet_address:
                    return {
                        "rank": rank,
                        "balance": float(bal),
                        "network": net
                    }
                rank += 1
    except FileNotFoundError:
        print("CSV file not found. Maybe it's not created yet.")
    except Exception as e:
        print(f"Error reading CSV: {e}")
    return None

@app.route("/check-rank-current", methods=["POST"])
def check_rank_curr():
    try:
        data = request.json
        wallet_address = data.get("wallet_address")
        if not wallet_address:
            return jsonify({"message": "Wallet address is required"}), 400

        info = get_rank_from_csv(wallet_address, CSV_FILE_PATH)
        if info is None:
            return jsonify({"message": "Wallet address not found"}), 404
        
        return jsonify({
            "rank": info["rank"],
            "balance": info["balance"],
            "network": info["network"]
        })
    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}"}), 500
    
@app.route("/check-rank-old", methods=["POST"])
def check_rank_old():
    try:
        data = request.json
        wallet_address = data.get("wallet_address")
        if not wallet_address:
            return jsonify({"message": "Wallet address is required"}), 400

        info = get_rank_from_csv(wallet_address, CSV_FILE_PATH_OLD)
        if info is None:
            return jsonify({"message": "Wallet address not found"}), 404
        
        return jsonify({
            "rank": info["rank"],
            "balance": info["balance"],
            "network": info["network"]
        })
    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}"}), 500    

# --------------------------------------------------------------------
# Ana Sayfa
# --------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

# --------------------------------------------------------------------
# Manuel CSV Oluşturma Rota (İsteğe Bağlı)
# --------------------------------------------------------------------
@app.route("/csv", methods=["GET"])
def show_csv_in_html():
    try:
        # CSV güncelle (opsiyonel)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(save_holders_to_csv())
        loop.close()

        # CSV’yi oku
        table_rows = []
        with open(CSV_FILE_PATH, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                table_rows.append(row)

        # Sonra bir template’e bunu aktarabilir veya
        # basitçe string oluşturup return edebilirsiniz.
        html = "<html><body><table border='1'>"
        for i, row in enumerate(table_rows):
            html += "<tr>"
            for col in row:
                if i == 0:
                    html += f"<th>{col}</th>"
                else:
                    html += f"<td>{col}</td>"
            html += "</tr>"
        html += "</table></body></html>"

        return html
    except Exception as e:
        return f"<p>Error: {e}</p>", 500
    
@app.route("/get-1360th-balance", methods=["GET"])
def get_1360th_balance():
    try:
        with open(CSV_FILE_PATH, mode="r", encoding="utf-8") as csvfile:
            reader = list(csv.reader(csvfile))
            header = reader[0]  # ["Address", "Balance", "Network"]
            rows = reader[1:]   # Geri kalan satırlar

            # 1360. sırayı (Python index = 1359) alalım
            if len(rows) >= 1360:
                row_1360 = rows[1359]  # 0 bazlı index
                # row_1360 = [ address, balance, network ]
                address_1360 = row_1360[0]
                balance_1360 = float(row_1360[1])
                network_1360 = row_1360[2]

                return jsonify({
                    "message": "Success",
                    "address": address_1360,
                    "balance": balance_1360,
                    "network": network_1360
                }), 200
            else:
                return jsonify({"message": "Not enough holders to reach 1360th rank"}), 404
    except Exception as e:
        return jsonify({"message": str(e)}), 500    

# -----------
# Uygulama Başlatma
# --------------------------------------------------------------------
if __name__ == "__main__":
    #job_update_csv() 
    #generate_csv()
    app.run(debug=True, use_reloader=False)