from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from enum import Enum
import uuid
from datetime import datetime

app = FastAPI(title="Poker Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Keep for local development
        "https://poker-tracker-one.vercel.app"  # Add your Vercel URL
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enums for validation
class PaymentMethod(str, Enum):
    CASH = "Cash"
    VENMO = "Venmo"
    APPLE_PAY = "Apple Pay"
    ZELLE = "Zelle"
    OTHER = "Other"

class TransactionType(str, Enum):
    BUY_IN = "buy-in"
    REBUY = "rebuy"

# Data models
class PlayerCreate(BaseModel):
    name: str

class Payment(BaseModel):
    id: str
    amount: float
    method: PaymentMethod
    type: TransactionType
    dealer_fee_applied: bool
    timestamp: datetime
    status: str = "pending" 

class BuyInRequest(BaseModel):
    amount: float
    method: PaymentMethod

class RebuyRequest(BaseModel):
    player_name: str
    amount: float
    method: PaymentMethod

class Player(BaseModel):
    id: str
    name: str
    total: float = 0.0
    payments: List[Payment] = []
    created_at: datetime

# NEW: Cash Out Models
class CashOut(BaseModel):
    id: str
    player_id: str
    amount: float
    timestamp: datetime
    reason: Optional[str] = "Player cashed out"
    confirmed: bool = False

class CashOutRequest(BaseModel):
    amount: float
    reason: Optional[str] = "Player cashed out"

# Game settings
DEALER_FEE = 35.0

# In-memory storage
players_db = {}
cash_outs_db = {}  # NEW: Store cash outs

@app.get("/")
def root():
    return {"message": "🃏 Poker Tracker API is running!"}

@app.get("/api/health") 
def health_check():
    return {"status": "healthy", "players": len(players_db)}

@app.get("/api/test")
def test():
    return {"status": "success", "message": "Backend connected!"}

@app.post("/api/players", response_model=Player)
def create_player(player_data: PlayerCreate):
    player_id = str(uuid.uuid4())
    new_player = Player(
        id=player_id,
        name=player_data.name,
        created_at=datetime.now()
    )
    players_db[player_id] = new_player.dict()
    return new_player

@app.get("/api/players", response_model=List[Player])
def get_players():
    return list(players_db.values())

@app.post("/api/players/{player_id}/buyin", response_model=Player)
def add_buyin(player_id: str, buyin_data: BuyInRequest):
    if player_id not in players_db:
        raise HTTPException(status_code=404, detail="Player not found")
    
    player = players_db[player_id]
    
    # Determine if this is first buy-in (apply dealer fee)
    has_previous_buyin = any(
        payment["type"] == TransactionType.BUY_IN 
        for payment in player["payments"]
    )
    
    # Create payment record
    payment_id = str(uuid.uuid4())
    dealer_fee_applied = not has_previous_buyin  # Apply fee only on first buy-in
    
    new_payment = Payment(
        id=payment_id,
        amount=buyin_data.amount,
        method=buyin_data.method,
        type=TransactionType.BUY_IN,
        dealer_fee_applied=dealer_fee_applied,
        timestamp=datetime.now(),
        status="pending"
    )
    
    # Update player
    player["payments"].append(new_payment.dict())
    # ONLY COUNT CONFIRMED PAYMENTS IN PLAYER TOTAL
    player["total"] = sum(
        payment["amount"] - (DEALER_FEE if payment["dealer_fee_applied"] else 0)
        for payment in player["payments"]
        if payment.get("status", "confirmed") == "confirmed"
    )
    
    players_db[player_id] = player
    return Player(**player)

@app.get("/api/game-stats")
def get_game_stats():
    total_pot = 0
    total_dealer_fees = 0
    total_buy_ins = 0
    total_cash_outs = 0
    payment_method_totals = {}
    
    for player in players_db.values():
        for payment in player["payments"]:
            # ONLY COUNT CONFIRMED PAYMENTS
            if payment.get("status", "confirmed") == "confirmed":
                # Add to pot (minus dealer fee)
                amount_to_pot = payment["amount"] - (DEALER_FEE if payment["dealer_fee_applied"] else 0)
                total_pot += amount_to_pot
                
                # Count dealer fees
                if payment["dealer_fee_applied"]:
                    total_dealer_fees += DEALER_FEE
                
                # Count total buy-ins
                total_buy_ins += payment["amount"]
                
                # Payment method breakdown
                method = payment["method"]
                if method not in payment_method_totals:
                    payment_method_totals[method] = {"total": 0, "count": 0}
                payment_method_totals[method]["total"] += payment["amount"]
                payment_method_totals[method]["count"] += 1
    
    # Subtract confirmed cash outs from pot
    for player_cash_outs in cash_outs_db.values():
        for cash_out in player_cash_outs:
            if cash_out["confirmed"]:
                total_cash_outs += cash_out["amount"]
                total_pot -= cash_out["amount"]
    
    return {
        "total_pot": round(total_pot, 2),
        "total_dealer_fees": round(total_dealer_fees, 2),
        "total_buy_ins": round(total_buy_ins, 2),
        "total_cash_outs": round(total_cash_outs, 2),
        "player_count": len(players_db),
        "payment_method_breakdown": payment_method_totals
    }

@app.get("/api/players/{player_id}/payment-summary")
def get_player_payment_summary(player_id: str):
    if player_id not in players_db:
        raise HTTPException(status_code=404, detail="Player not found")
    
    player = players_db[player_id]
    payment_summary = {}
    
    for payment in player["payments"]:
        method = payment["method"]
        amount = payment["amount"]
        if method not in payment_summary:
            payment_summary[method] = {"total": 0, "count": 0}
        payment_summary[method]["total"] += amount
        payment_summary[method]["count"] += 1
    
    return {
        "player_id": player_id,
        "player_name": player["name"],
        "payment_summary": payment_summary,
        "total_in_pot": player["total"]
    }

@app.post("/api/rebuys")
def process_rebuy(rebuy_data: RebuyRequest):
    # Try to find existing player by name
    player_id = None
    for pid, player in players_db.items():
        if player["name"].lower() == rebuy_data.player_name.lower():
            player_id = pid
            break
    
    # If player doesn't exist, CREATE THEM automatically
    if not player_id:
        player_id = str(uuid.uuid4())
        new_player = Player(
            id=player_id,
            name=rebuy_data.player_name,
            created_at=datetime.now()
        )
        players_db[player_id] = new_player.dict()
        player = players_db[player_id]
        is_new_player = True
    else:
        player = players_db[player_id]
        is_new_player = False
    
    # SMART DETECTION: Check if this is their first transaction
    has_any_previous_transactions = len(player["payments"]) > 0
    
    # If no previous transactions, this is a buy-in (apply dealer fee)
    # If they have previous transactions, this is a rebuy (no dealer fee)
    is_first_buyin = not has_any_previous_transactions
    transaction_type = TransactionType.BUY_IN if is_first_buyin else TransactionType.REBUY
    
    payment_id = str(uuid.uuid4())
    new_payment = Payment(
        id=payment_id,
        amount=rebuy_data.amount,
        method=rebuy_data.method,
        type=transaction_type,
        dealer_fee_applied=is_first_buyin,
        timestamp=datetime.now(),
        status="pending"
    )
    
    # Update player
    player["payments"].append(new_payment.dict())
    # ONLY COUNT CONFIRMED PAYMENTS IN PLAYER TOTAL
    player["total"] = sum(
        payment["amount"] - (DEALER_FEE if payment["dealer_fee_applied"] else 0)
        for payment in player["payments"]
        if payment.get("status", "confirmed") == "confirmed"
    )
    
    players_db[player_id] = player
    
    # Return helpful message
    if is_new_player:
        message = f"Welcome {player['name']}! First buy-in processed (${DEALER_FEE} dealer fee applied)"
    else:
        transaction_word = "buy-in" if is_first_buyin else "rebuy"
        fee_message = f" (${DEALER_FEE} dealer fee applied)" if is_first_buyin else " (no dealer fee)"
        message = f"{transaction_word.title()} processed for {player['name']}{fee_message}"
    
    return {
        "success": True, 
        "message": message,
        "is_new_player": is_new_player,
        "is_first_buyin": is_first_buyin,
        "dealer_fee_applied": is_first_buyin,
        "amount_to_pot": rebuy_data.amount - (DEALER_FEE if is_first_buyin else 0)
    }

@app.get("/api/rebuys/recent")
def get_recent_rebuys():
    recent_rebuys = []
    for player in players_db.values():
        for payment in player["payments"]:
            if payment["type"] == TransactionType.REBUY:
                recent_rebuys.append({
                    "player_name": player["name"],
                    "amount": payment["amount"],
                    "method": payment["method"],
                    "timestamp": payment["timestamp"]
                })
    
    recent_rebuys.sort(key=lambda x: x["timestamp"], reverse=True)
    return recent_rebuys[:5]

@app.delete("/api/players/{player_id}/payments/{payment_id}")
def delete_payment(player_id: str, payment_id: str):
    if player_id not in players_db:
        raise HTTPException(status_code=404, detail="Player not found")
    
    player = players_db[player_id]
    
    # Find and remove the payment
    payment_to_remove = None
    for i, payment in enumerate(player["payments"]):
        if payment["id"] == payment_id:
            payment_to_remove = player["payments"].pop(i)
            break
    
    if not payment_to_remove:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    # Recalculate player total - ONLY COUNT CONFIRMED PAYMENTS
    player["total"] = sum(
        payment["amount"] - (DEALER_FEE if payment["dealer_fee_applied"] else 0)
        for payment in player["payments"]
        if payment.get("status", "confirmed") == "confirmed"
    )
    
    players_db[player_id] = player
    return {"success": True, "message": f"Removed ${payment_to_remove['amount']} {payment_to_remove['type']} for {player['name']}"}

@app.get("/api/transactions/recent")
def get_recent_transactions():
    """Get all recent transactions across all players for admin view"""
    all_transactions = []
    for player in players_db.values():
        for payment in player["payments"]:
            all_transactions.append({
                "id": payment["id"],
                "player_id": player["id"],
                "player_name": player["name"],
                "amount": payment["amount"],
                "method": payment["method"],
                "type": payment["type"],
                "dealer_fee_applied": payment["dealer_fee_applied"],
                "timestamp": payment["timestamp"],
                "status": payment.get("status", "confirmed")  # Include status
            })
    
    # Sort by timestamp, most recent first
    all_transactions.sort(key=lambda x: x["timestamp"], reverse=True)
    return all_transactions[:20]  # Return last 20 transactions

@app.delete("/api/players/{player_id}")
def delete_player(player_id: str):
    if player_id not in players_db:
        raise HTTPException(status_code=404, detail="Player not found")
    
    player = players_db[player_id]
    player_name = player["name"]
    player_total = player["total"]
    transaction_count = len(player["payments"])
    
    # Remove the player
    del players_db[player_id]
    
    # Also remove any cash outs for this player
    if player_id in cash_outs_db:
        del cash_outs_db[player_id]
    
    return {
        "success": True, 
        "message": f"Deleted {player_name} (${player_total} removed from pot, {transaction_count} transactions deleted)"
    }

@app.put("/api/players/{player_id}/payments/{payment_id}/confirm")
def confirm_payment(player_id: str, payment_id: str):
    if player_id not in players_db:
        raise HTTPException(status_code=404, detail="Player not found")
    
    player = players_db[player_id]
    
    # Find the payment
    payment_found = False
    for payment in player["payments"]:
        if payment["id"] == payment_id:
            if payment.get("status", "confirmed") == "confirmed":
                raise HTTPException(status_code=400, detail="Payment already confirmed")
            
            payment["status"] = "confirmed"
            payment["confirmed_at"] = datetime.now().isoformat()
            payment_found = True
            break
    
    if not payment_found:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    # Recalculate player total after confirming payment
    player["total"] = sum(
        payment["amount"] - (DEALER_FEE if payment["dealer_fee_applied"] else 0)
        for payment in player["payments"]
        if payment.get("status", "confirmed") == "confirmed"
    )
    
    players_db[player_id] = player
    
    return {
        "success": True,
        "message": f"Payment confirmed for {player['name']}"
    }

@app.get("/api/pending-payments")
def get_pending_payments():
    pending_payments = []
    
    for player_id, player in players_db.items():
        for payment in player["payments"]:
            if payment.get("status", "confirmed") == "pending":  # Default old payments to confirmed
                pending_payments.append({
                    **payment,
                    "player_id": player_id,
                    "player_name": player["name"]
                })
    
    # Sort by timestamp (newest first)
    pending_payments.sort(key=lambda x: x["timestamp"], reverse=True)
    return pending_payments

# NEW CASH OUT ENDPOINTS

@app.post("/api/players/{player_id}/cashout")
async def create_cash_out(player_id: str, request: CashOutRequest):
    """Create a cash out request for a player"""
    if player_id not in players_db:
        raise HTTPException(status_code=404, detail="Player not found")
    
    player = players_db[player_id]
    cash_out_amount = request.amount
    
    # Validate cash out amount
    if cash_out_amount <= 0:
        raise HTTPException(status_code=400, detail="Cash out amount must be positive")
    
    if cash_out_amount > player["total"]:
        raise HTTPException(status_code=400, detail="Cannot cash out more than player's total")
    
    # Create cash out record
    cash_out_id = str(uuid.uuid4())
    cash_out = {
        "id": cash_out_id,
        "player_id": player_id,
        "amount": cash_out_amount,
        "timestamp": datetime.now().isoformat(),
        "reason": request.reason,
        "confirmed": False
    }
    
    if player_id not in cash_outs_db:
        cash_outs_db[player_id] = []
    
    cash_outs_db[player_id].append(cash_out)
    
    return {"success": True, "cash_out_id": cash_out_id}

@app.get("/api/pending-cashouts")
async def get_pending_cash_outs():
    """Get all pending cash outs for admin confirmation"""
    pending = []
    for player_id, player_cash_outs in cash_outs_db.items():
        for cash_out in player_cash_outs:
            if not cash_out["confirmed"]:
                player_name = players_db[player_id]["name"] if player_id in players_db else "Unknown"
                pending.append({
                    **cash_out,
                    "player_name": player_name
                })
    
    # Sort by timestamp (newest first)
    pending.sort(key=lambda x: x["timestamp"], reverse=True)
    return pending

@app.put("/api/cashouts/{cash_out_id}/confirm")
async def confirm_cash_out(cash_out_id: str):
    """Confirm a cash out and update player total"""
    # Find the cash out
    cash_out_found = None
    player_id_found = None
    
    for player_id, player_cash_outs in cash_outs_db.items():
        for cash_out in player_cash_outs:
            if cash_out["id"] == cash_out_id and not cash_out["confirmed"]:
                cash_out_found = cash_out
                player_id_found = player_id
                break
        if cash_out_found:
            break
    
    if not cash_out_found:
        raise HTTPException(status_code=404, detail="Cash out not found or already confirmed")
    
    # Confirm the cash out
    cash_out_found["confirmed"] = True
    cash_out_found["confirmed_at"] = datetime.now().isoformat()
    
    # Reduce player's total
    if player_id_found in players_db:
        players_db[player_id_found]["total"] -= cash_out_found["amount"]
        
        # Ensure total doesn't go negative
        if players_db[player_id_found]["total"] < 0:
            players_db[player_id_found]["total"] = 0
    
    return {"success": True}

@app.get("/api/cashouts/history")
async def get_cash_out_history():
    """Get all confirmed cash outs for reconciliation"""
    confirmed_cash_outs = []
    for player_id, player_cash_outs in cash_outs_db.items():
        for cash_out in player_cash_outs:
            if cash_out["confirmed"]:
                player_name = players_db[player_id]["name"] if player_id in players_db else "Unknown"
                confirmed_cash_outs.append({
                    **cash_out,
                    "player_name": player_name
                })
    return sorted(confirmed_cash_outs, key=lambda x: x["timestamp"], reverse=True)
if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)