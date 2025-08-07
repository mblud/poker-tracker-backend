from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from enum import Enum
import uuid
from datetime import datetime

# ADD DATABASE IMPORTS
import os
from sqlalchemy import create_engine, Column, String, Float, DateTime, Boolean, Text, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

app = FastAPI(title="Poker Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://poker-degens.vercel.app",  # Your actual Vercel URL
        "*"  # Temporary wildcard for testing
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DATABASE SETUP
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    
    # Database Models
    class PlayerDB(Base):
        __tablename__ = "players"
        
        id = Column(String, primary_key=True)
        name = Column(String, nullable=False)
        total = Column(Float, default=0.0)
        created_at = Column(DateTime, nullable=False)
    
    class PaymentDB(Base):
        __tablename__ = "payments"
        
        id = Column(String, primary_key=True)
        player_id = Column(String, nullable=False)
        amount = Column(Float, nullable=False)
        method = Column(String, nullable=False)
        type = Column(String, nullable=False)
        dealer_fee_applied = Column(Boolean, default=False)
        status = Column(String, default="pending")
        timestamp = Column(DateTime, nullable=False)
    
    class CashOutDB(Base):
        __tablename__ = "cashouts"
        
        id = Column(String, primary_key=True)
        player_id = Column(String, nullable=False)
        amount = Column(Float, nullable=False)
        timestamp = Column(DateTime, nullable=False)
        reason = Column(String, default="Player cashed out")
        confirmed = Column(Boolean, default=False)
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    print("✅ Database connected and tables created!")
    
else:
    # Fallback to in-memory (development)
    print("⚠️  Warning: No database configured, using in-memory storage")

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

# Cash Out Models
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

# In-memory storage (fallback only)
if not DATABASE_URL:
    players_db = {}
    cash_outs_db = {}

@app.get("/")
def root():
    return {"message": "🃏 Poker Tracker API is running!"}

@app.get("/api/health") 
def health_check():
    if DATABASE_URL:
        try:
            with SessionLocal() as db:
                player_count = db.query(PlayerDB).count()
                return {"status": "healthy", "players": player_count, "database": "connected"}
        except Exception as e:
            return {"status": "error", "database": "disconnected", "error": str(e)}
    else:
        return {"status": "healthy", "players": len(players_db), "database": "in-memory"}

@app.get("/api/test")
def test():
    return {"status": "success", "message": "Backend connected!"}

@app.post("/api/players", response_model=Player)
def create_player(player_data: PlayerCreate):
    player_id = str(uuid.uuid4())
    
    if DATABASE_URL:
        with SessionLocal() as db:
            db_player = PlayerDB(
                id=player_id,
                name=player_data.name,
                total=0.0,
                created_at=datetime.now()
            )
            db.add(db_player)
            db.commit()
            db.refresh(db_player)
            
            return Player(
                id=db_player.id,
                name=db_player.name,
                total=db_player.total,
                payments=[],
                created_at=db_player.created_at
            )
    else:
        new_player = Player(
            id=player_id,
            name=player_data.name,
            created_at=datetime.now()
        )
        players_db[player_id] = new_player.dict()
        return new_player

@app.get("/api/players", response_model=List[Player])
def get_players():
    if DATABASE_URL:
       with SessionLocal() as db:
        players = db.query(PlayerDB).all()
        result = []
        
        for player in players:
            # Get ALL payments for this player
            payments = db.query(PaymentDB).filter(PaymentDB.player_id == player.id).all()
            payment_list = [
                Payment(
                    id=p.id,
                    amount=p.amount,
                    method=p.method,
                    type=p.type,
                    dealer_fee_applied=p.dealer_fee_applied,
                    timestamp=p.timestamp,
                    status=p.status
                ) for p in payments
            ]
            
            # FIXED: Recalculate total from confirmed payments only
            confirmed_total = sum(
                p.amount - (DEALER_FEE if p.dealer_fee_applied else 0)
                for p in payments 
                if p.status == "confirmed"
            )
            
            # Update the database if it's wrong
            if abs(player.total - confirmed_total) > 0.01:  # Fix rounding issues
                player.total = confirmed_total
                db.commit()
            
            result.append(Player(
                id=player.id,
                name=player.name,
                total=confirmed_total,  # Use calculated total, not stored total
                payments=payment_list,
                created_at=player.created_at
            ))
        
        return result
    else:
        return list(players_db.values())

@app.post("/api/players/{player_id}/buyin", response_model=Player)
def add_buyin(player_id: str, buyin_data: BuyInRequest):
    if DATABASE_URL:
        with SessionLocal() as db:
            db_player = db.query(PlayerDB).filter(PlayerDB.id == player_id).first()
            if not db_player:
                raise HTTPException(status_code=404, detail="Player not found")
            
            # Check if player has previous buy-ins
            has_previous_buyin = db.query(PaymentDB).filter(
                PaymentDB.player_id == player_id,
                PaymentDB.type == TransactionType.BUY_IN.value
            ).first() is not None
            
            # Create payment record
            payment_id = str(uuid.uuid4())
            dealer_fee_applied = not has_previous_buyin
            
            db_payment = PaymentDB(
                id=payment_id,
                player_id=player_id,
                amount=buyin_data.amount,
                method=buyin_data.method.value,
                type=TransactionType.BUY_IN.value,
                dealer_fee_applied=dealer_fee_applied,
                timestamp=datetime.now(),
                status="pending"
            )
            
            db.add(db_payment)
            
            # Update player total (only confirmed payments)
            confirmed_payments = db.query(PaymentDB).filter(
                PaymentDB.player_id == player_id,
                PaymentDB.status == "confirmed"
            ).all()
            
            db_player.total = sum(
                payment.amount - (DEALER_FEE if payment.dealer_fee_applied else 0)
                for payment in confirmed_payments
            )
            
            db.commit()
            
            # Return updated player
            payments = db.query(PaymentDB).filter(PaymentDB.player_id == player_id).all()
            payment_list = [
                Payment(
                    id=p.id,
                    amount=p.amount,
                    method=p.method,
                    type=p.type,
                    dealer_fee_applied=p.dealer_fee_applied,
                    timestamp=p.timestamp,
                    status=p.status
                ) for p in payments
            ]
            
            return Player(
                id=db_player.id,
                name=db_player.name,
                total=db_player.total,
                payments=payment_list,
                created_at=db_player.created_at
            )
    else:
        # Fallback to in-memory
        if player_id not in players_db:
            raise HTTPException(status_code=404, detail="Player not found")
        
        player = players_db[player_id]
        
        has_previous_buyin = any(
            payment["type"] == TransactionType.BUY_IN 
            for payment in player["payments"]
        )
        
        payment_id = str(uuid.uuid4())
        dealer_fee_applied = not has_previous_buyin
        
        new_payment = Payment(
            id=payment_id,
            amount=buyin_data.amount,
            method=buyin_data.method,
            type=TransactionType.BUY_IN,
            dealer_fee_applied=dealer_fee_applied,
            timestamp=datetime.now(),
            status="pending"
        )
        
        player["payments"].append(new_payment.dict())
        player["total"] = sum(
            payment["amount"] - (DEALER_FEE if payment["dealer_fee_applied"] else 0)
            for payment in player["payments"]
            if payment.get("status", "confirmed") == "confirmed"
        )
        
        players_db[player_id] = player
        return Player(**player)

@app.get("/api/game-stats")
def get_game_stats():
   if DATABASE_URL:
    with SessionLocal() as db:
        players = db.query(PlayerDB).all()
        payments = db.query(PaymentDB).filter(PaymentDB.status == "confirmed").all()
        confirmed_cash_outs = db.query(CashOutDB).filter(CashOutDB.confirmed == True).all()
        
        # Calculate totals
        total_pot = sum(p.total for p in players)  # This should already reflect cash outs
        total_dealer_fees = sum(DEALER_FEE for p in payments if p.dealer_fee_applied)
        total_buy_ins = sum(p.amount for p in payments)
        total_cash_outs = sum(c.amount for c in confirmed_cash_outs)
        
        # Payment method breakdown
        payment_method_totals = {}
        for payment in payments:
            method = payment.method
            if method not in payment_method_totals:
                payment_method_totals[method] = {"total": 0, "count": 0}
            payment_method_totals[method]["total"] += payment.amount
            payment_method_totals[method]["count"] += 1
        
        return {
            "total_pot": round(total_pot, 2),
            "total_dealer_fees": round(total_dealer_fees, 2),
            "total_buy_ins": round(total_buy_ins, 2),
            "total_cash_outs": round(total_cash_outs, 2),
            "player_count": len(players),
            "payment_method_breakdown": payment_method_totals
        }

@app.get("/api/players/{player_id}/payment-summary")
def get_player_payment_summary(player_id: str):
    if DATABASE_URL:
        with SessionLocal() as db:
            db_player = db.query(PlayerDB).filter(PlayerDB.id == player_id).first()
            if not db_player:
                raise HTTPException(status_code=404, detail="Player not found")
            
            payments = db.query(PaymentDB).filter(PaymentDB.player_id == player_id).all()
            payment_summary = {}
            
            for payment in payments:
                method = payment.method
                amount = payment.amount
                if method not in payment_summary:
                    payment_summary[method] = {"total": 0, "count": 0}
                payment_summary[method]["total"] += amount
                payment_summary[method]["count"] += 1
            
            return {
                "player_id": player_id,
                "player_name": db_player.name,
                "payment_summary": payment_summary,
                "total_in_pot": db_player.total
            }
    else:
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
    if DATABASE_URL:
       with SessionLocal() as db:
        # Try to find existing player
        db_player = db.query(PlayerDB).filter(PlayerDB.name.ilike(rebuy_data.player_name)).first()
        
        if not db_player:
            # Create new player with $0 total (payments are pending!)
            player_id = str(uuid.uuid4())
            db_player = PlayerDB(
                id=player_id,
                name=rebuy_data.player_name,
                total=0.0,  # ← IMPORTANT: Start with $0, only update when confirmed
                created_at=datetime.now()
            )
            db.add(db_player)
            db.commit()
            is_new_player = True
        else:
            is_new_player = False
        
        # Check if first transaction
        has_any_previous_transactions = db.query(PaymentDB).filter(
            PaymentDB.player_id == db_player.id
        ).first() is not None
        
        is_first_buyin = not has_any_previous_transactions
        transaction_type = TransactionType.BUY_IN if is_first_buyin else TransactionType.REBUY
        
        # Create payment as PENDING (don't update player total yet!)
        payment_id = str(uuid.uuid4())
        db_payment = PaymentDB(
            id=payment_id,
            player_id=db_player.id,
            amount=rebuy_data.amount,
            method=rebuy_data.method.value,
            type=transaction_type.value,
            dealer_fee_applied=is_first_buyin,
            timestamp=datetime.now(),
            status="pending"  # ← IMPORTANT: Pending until host confirms
        )
        
        db.add(db_payment)
        db.commit()
        
        # DON'T UPDATE db_player.total here - only update when confirmed!
        
        return {
            "success": True, 
            "message": f"Payment submitted for {db_player.name} - waiting for host approval",
            "is_new_player": is_new_player,
            "is_first_buyin": is_first_buyin,
            "dealer_fee_applied": is_first_buyin,
            "amount_to_pot": rebuy_data.amount - (DEALER_FEE if is_first_buyin else 0)
        }

@app.get("/api/rebuys/recent")
def get_recent_rebuys():
    if DATABASE_URL:
        with SessionLocal() as db:
            # FIXED: No JOIN, separate queries
            recent_payments = db.query(PaymentDB).filter(
                PaymentDB.type == TransactionType.REBUY.value
            ).order_by(PaymentDB.timestamp.desc()).limit(5).all()
            
            result = []
            for payment in recent_payments:
                # Get player name separately - NO JOIN
                player = db.query(PlayerDB).filter(PlayerDB.id == payment.player_id).first()
                player_name = player.name if player else "Unknown Player"
                
                result.append({
                    "player_name": player_name,
                    "amount": payment.amount,
                    "method": payment.method,
                    "timestamp": payment.timestamp
                })
            
            return result
    else:
        # Your existing in-memory code stays the same
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
    if DATABASE_URL:
        with SessionLocal() as db:
            db_player = db.query(PlayerDB).filter(PlayerDB.id == player_id).first()
            if not db_player:
                raise HTTPException(status_code=404, detail="Player not found")
            
            payment = db.query(PaymentDB).filter(PaymentDB.id == payment_id).first()
            if not payment:
                raise HTTPException(status_code=404, detail="Payment not found")
            
            payment_amount = payment.amount
            payment_type = payment.type
            
            # Delete payment
            db.delete(payment)
            
            # Recalculate player total
            remaining_payments = db.query(PaymentDB).filter(
                PaymentDB.player_id == player_id,
                PaymentDB.status == "confirmed"
            ).all()
            
            db_player.total = sum(
                p.amount - (DEALER_FEE if p.dealer_fee_applied else 0)
                for p in remaining_payments
            )
            
            db.commit()
            
            return {"success": True, "message": f"Removed ${payment_amount} {payment_type} for {db_player.name}"}
    else:
        if player_id not in players_db:
            raise HTTPException(status_code=404, detail="Player not found")
        
        player = players_db[player_id]
        
        payment_to_remove = None
        for i, payment in enumerate(player["payments"]):
            if payment["id"] == payment_id:
                payment_to_remove = player["payments"].pop(i)
                break
        
        if not payment_to_remove:
            raise HTTPException(status_code=404, detail="Payment not found")
        
        player["total"] = sum(
            payment["amount"] - (DEALER_FEE if payment["dealer_fee_applied"] else 0)
            for payment in player["payments"]
            if payment.get("status", "confirmed") == "confirmed"
        )
        
        players_db[player_id] = player
        return {"success": True, "message": f"Removed ${payment_to_remove['amount']} {payment_to_remove['type']} for {player['name']}"}

@app.get("/api/transactions/recent")
def get_recent_transactions():
    if DATABASE_URL:
        with SessionLocal() as db:
            # FIXED: No JOIN, separate queries
            payments = db.query(PaymentDB).order_by(PaymentDB.timestamp.desc()).limit(20).all()
            result = []
            
            for payment in payments:
                # Get player name separately - NO JOIN
                player = db.query(PlayerDB).filter(PlayerDB.id == payment.player_id).first()
                player_name = player.name if player else "Unknown Player"
                
                result.append({
                    "id": payment.id,
                    "player_id": payment.player_id,
                    "player_name": player_name,
                    "amount": payment.amount,
                    "method": payment.method,
                    "type": payment.type,
                    "dealer_fee_applied": payment.dealer_fee_applied,
                    "timestamp": payment.timestamp,
                    "status": payment.status
                })
            
            return result
    else:
        # Your existing in-memory code stays the same
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
                    "status": payment.get("status", "confirmed")
                })
        
        all_transactions.sort(key=lambda x: x["timestamp"], reverse=True)
        return all_transactions[:20]

@app.delete("/api/players/{player_id}")
def delete_player(player_id: str):
    if DATABASE_URL:
        with SessionLocal() as db:
            db_player = db.query(PlayerDB).filter(PlayerDB.id == player_id).first()
            if not db_player:
                raise HTTPException(status_code=404, detail="Player not found")
            
            player_name = db_player.name
            player_total = db_player.total
            
            # Count transactions
            transaction_count = db.query(PaymentDB).filter(PaymentDB.player_id == player_id).count()
            
            # Delete all payments first
            db.query(PaymentDB).filter(PaymentDB.player_id == player_id).delete()
            # Delete all cash outs
            db.query(CashOutDB).filter(CashOutDB.player_id == player_id).delete()
            # Delete player
            db.delete(db_player)
            db.commit()
            
            return {
                "success": True, 
                "message": f"Deleted {player_name} (${player_total} removed from pot, {transaction_count} transactions deleted)"
            }
    else:
        if player_id not in players_db:
            raise HTTPException(status_code=404, detail="Player not found")
        
        player = players_db[player_id]
        player_name = player["name"]
        player_total = player["total"]
        transaction_count = len(player["payments"])
        
        del players_db[player_id]
        
        if player_id in cash_outs_db:
            del cash_outs_db[player_id]
        
        return {
            "success": True, 
            "message": f"Deleted {player_name} (${player_total} removed from pot, {transaction_count} transactions deleted)"
        }

@app.put("/api/players/{player_id}/payments/{payment_id}/confirm")
def confirm_payment(player_id: str, payment_id: str):
    if DATABASE_URL:
        with SessionLocal() as db:
            # Find the payment
            payment = db.query(PaymentDB).filter(PaymentDB.id == payment_id).first()
            if not payment:
                raise HTTPException(status_code=404, detail="Payment not found")
            
            if payment.status == "confirmed":
                raise HTTPException(status_code=400, detail="Payment already confirmed")
            
            # Find the player
            player = db.query(PlayerDB).filter(PlayerDB.id == player_id).first()
            if not player:
                raise HTTPException(status_code=404, detail="Player not found")
            
            # Confirm the payment
            payment.status = "confirmed"
            
            # FIXED: Properly recalculate player total from ALL confirmed payments
            all_confirmed_payments = db.query(PaymentDB).filter(
                PaymentDB.player_id == player_id,
                PaymentDB.status == "confirmed"
            ).all()
            
            # Calculate new total: amount minus dealer fee (if applied)
            new_total = 0.0
            for p in all_confirmed_payments:
                amount_to_pot = p.amount - (DEALER_FEE if p.dealer_fee_applied else 0)
                new_total += amount_to_pot
            
            player.total = new_total
            db.commit()
            
            return {
                "success": True,
                "message": f"Payment confirmed for {player.name}. New total: ${player.total:.2f}"
            }
    else:
        # In-memory fallback - same logic
        if player_id not in players_db:
            raise HTTPException(status_code=404, detail="Player not found")
        
        player = players_db[player_id]
        
        payment_found = False
        for payment in player["payments"]:
            if payment["id"] == payment_id:
                if payment.get("status", "confirmed") == "confirmed":
                    raise HTTPException(status_code=400, detail="Payment already confirmed")
                
                payment["status"] = "confirmed"
                payment_found = True
                break
        
        if not payment_found:
            raise HTTPException(status_code=404, detail="Payment not found")
        
        # Recalculate total from all confirmed payments
        player["total"] = sum(
            payment["amount"] - (DEALER_FEE if payment["dealer_fee_applied"] else 0)
            for payment in player["payments"]
            if payment.get("status", "confirmed") == "confirmed"
        )
        
        players_db[player_id] = player
        
        return {
            "success": True,
            "message": f"Payment confirmed for {player['name']}. New total: ${player['total']:.2f}"
        }

@app.get("/api/pending-payments")
def get_pending_payments():
    if DATABASE_URL:
        with SessionLocal() as db:
            # FIXED: No JOIN, separate queries
            pending_payments = db.query(PaymentDB).filter(PaymentDB.status == "pending").all()
            result = []
            
            for payment in pending_payments:
                # Get player name separately - NO JOIN
                player = db.query(PlayerDB).filter(PlayerDB.id == payment.player_id).first()
                player_name = player.name if player else "Unknown Player"
                
                result.append({
                    "id": payment.id,
                    "player_id": payment.player_id,
                    "player_name": player_name,
                    "amount": payment.amount,
                    "method": payment.method,
                    "type": payment.type,
                    "dealer_fee_applied": payment.dealer_fee_applied,
                    "timestamp": payment.timestamp,
                    "status": payment.status
                })
            
            return result
    else:
        # Your existing in-memory code stays the same
        pending_payments = []
        for player_id, player in players_db.items():
            for payment in player["payments"]:
                if payment.get("status", "confirmed") == "pending":
                    pending_payments.append({
                        **payment,
                        "player_id": player_id,
                        "player_name": player["name"]
                    })
        
        pending_payments.sort(key=lambda x: x["timestamp"], reverse=True)
        return pending_payments

# 🔥 FIXED: Cash out creation
@app.post("/api/players/{player_id}/cashout")
async def create_cash_out(player_id: str, request: CashOutRequest):
    if DATABASE_URL:
        with SessionLocal() as db:
            db_player = db.query(PlayerDB).filter(PlayerDB.id == player_id).first()
            if not db_player:
                raise HTTPException(status_code=404, detail="Player not found")
            
            cash_out_amount = request.amount
            
            if cash_out_amount <= 0:
                raise HTTPException(status_code=400, detail="Cash out amount must be positive")
            
            # 🚀 POKER LOGIC: Calculate total pot - players can cash out winnings!
            all_players = db.query(PlayerDB).all()
            total_pot = sum(p.total for p in all_players)
            
            print(f"🔍 DEBUG: Player {db_player.name} wants ${cash_out_amount}")
            print(f"🔍 DEBUG: Player has ${db_player.total} in pot")
            print(f"🔍 DEBUG: Total pot is ${total_pot}")
            
            # Players can cash out up to the TOTAL POT (they can win!)
            if cash_out_amount > total_pot:
                error_msg = f"Cannot cash out ${cash_out_amount:.2f}. Total pot only has ${total_pot:.2f}"
                print(f"❌ DEBUG: {error_msg}")
                raise HTTPException(status_code=400, detail=error_msg)
            
            # Create the cash out request
            cash_out_id = str(uuid.uuid4())
            db_cash_out = CashOutDB(
                id=cash_out_id,
                player_id=player_id,
                amount=cash_out_amount,
                timestamp=datetime.now(),
                reason=request.reason or "Player cashed out",
                confirmed=False
            )
            
            db.add(db_cash_out)
            db.commit()
            
            print(f"✅ DEBUG: Cash out request created successfully")
            
            return {
                "success": True, 
                "cash_out_id": cash_out_id,
                "message": f"Cash out request for ${cash_out_amount:.2f} created"
            }
    else:
        # In-memory fallback - also fix this
        if player_id not in players_db:
            raise HTTPException(status_code=404, detail="Player not found")
        
        cash_out_amount = request.amount
        
        if cash_out_amount <= 0:
            raise HTTPException(status_code=400, detail="Cash out amount must be positive")
        
        # Calculate total pot for in-memory version
        total_pot = sum(player["total"] for player in players_db.values())
        
        if cash_out_amount > total_pot:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot cash out ${cash_out_amount:.2f}. Total pot only has ${total_pot:.2f}"
            )
        
        cash_out_id = str(uuid.uuid4())
        cash_out = {
            "id": cash_out_id,
            "player_id": player_id,
            "amount": cash_out_amount,
            "timestamp": datetime.now().isoformat(),
            "reason": request.reason or "Player cashed out",
            "confirmed": False
        }
        
        if player_id not in cash_outs_db:
            cash_outs_db[player_id] = []
        
        cash_outs_db[player_id].append(cash_out)
        
        return {"success": True, "cash_out_id": cash_out_id}

@app.get("/api/pending-cashouts")
async def get_pending_cash_outs():
    if DATABASE_URL:
        with SessionLocal() as db:
            # FIXED: No JOIN, separate queries
            pending = db.query(CashOutDB).filter(CashOutDB.confirmed == False).all()
            result = []
            
            for cash_out in pending:
                # Get player name separately - NO JOIN
                player = db.query(PlayerDB).filter(PlayerDB.id == cash_out.player_id).first()
                player_name = player.name if player else "Unknown Player"
                
                result.append({
                    "id": cash_out.id,
                    "player_id": cash_out.player_id,
                    "player_name": player_name,
                    "amount": cash_out.amount,
                    "timestamp": cash_out.timestamp.isoformat(),
                    "reason": cash_out.reason,
                    "confirmed": cash_out.confirmed
                })
            
            result.sort(key=lambda x: x["timestamp"], reverse=True)
            return result
    else:
        # Your existing in-memory code stays the same
        pending = []
        for player_id, player_cash_outs in cash_outs_db.items():
            for cash_out in player_cash_outs:
                if not cash_out["confirmed"]:
                    player_name = players_db[player_id]["name"] if player_id in players_db else "Unknown"
                    pending.append({
                        **cash_out,
                        "player_name": player_name
                    })
        
        pending.sort(key=lambda x: x["timestamp"], reverse=True)
        return pending

# 🔥 FIXED: Confirm cash out - properly sets player total to 0
@app.put("/api/cashouts/{cash_out_id}/confirm")
async def confirm_cash_out(cash_out_id: str):
    if DATABASE_URL:
        with SessionLocal() as db:
            # Find the cash out
            cash_out = db.query(CashOutDB).filter(CashOutDB.id == cash_out_id).first()
            if not cash_out or cash_out.confirmed:
                raise HTTPException(status_code=404, detail="Cash out not found or already confirmed")
            
            # Find the player
            player = db.query(PlayerDB).filter(PlayerDB.id == cash_out.player_id).first()
            if not player:
                raise HTTPException(status_code=404, detail="Player not found")
            
            # Confirm the cash out
            cash_out.confirmed = True
            
            # 🔥 FIXED: Set player total to 0 when they cash out (they're out of the game)
            old_total = player.total
            player.total = 0.0  # Player is completely out of the game
            
            db.commit()
            
            return {
                "success": True,
                "message": f"Cash out confirmed: {player.name} cashed out ${cash_out.amount}. Player is now out of the game.",
                "player_name": player.name,
                "cash_out_amount": cash_out.amount,
                "old_player_total": old_total,
                "new_player_total": 0.0
            }
    else:
        # In-memory fallback
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
        
        # 🔥 FIXED: Set player total to 0 (they're out of the game)
        if player_id_found in players_db:
            old_total = players_db[player_id_found]["total"]
            players_db[player_id_found]["total"] = 0.0  # Player is completely out
            
            return {
                "success": True,
                "message": f"Cash out confirmed: {players_db[player_id_found]['name']} cashed out ${cash_out_found['amount']}",
                "new_player_total": 0.0
            }
        
        return {"success": True}

# 🔥 NEW: Get recent confirmed cash outs for UI display
@app.get("/api/cashouts/recent")
async def get_recent_cash_outs():
    if DATABASE_URL:
        with SessionLocal() as db:
            # Get recent confirmed cash outs
            confirmed = db.query(CashOutDB).filter(
                CashOutDB.confirmed == True
            ).order_by(CashOutDB.timestamp.desc()).limit(10).all()
            
            result = []
            for cash_out in confirmed:
                # Get player name separately - NO JOIN
                player = db.query(PlayerDB).filter(PlayerDB.id == cash_out.player_id).first()
                player_name = player.name if player else "Unknown Player"
                
                result.append({
                    "id": cash_out.id,
                    "player_id": cash_out.player_id,
                    "player_name": player_name,
                    "amount": cash_out.amount,
                    "timestamp": cash_out.timestamp.isoformat(),
                    "reason": cash_out.reason,
                    "confirmed": cash_out.confirmed
                })
            
            return result
    else:
        confirmed_cash_outs = []
        for player_id, player_cash_outs in cash_outs_db.items():
            for cash_out in player_cash_outs:
                if cash_out["confirmed"]:
                    player_name = players_db[player_id]["name"] if player_id in players_db else "Unknown"
                    confirmed_cash_outs.append({
                        **cash_out,
                        "player_name": player_name
                    })
        return sorted(confirmed_cash_outs, key=lambda x: x["timestamp"], reverse=True)[:10]

@app.get("/api/debug/cashouts")
def debug_cash_outs():
    """Debug all cash outs and their impact on totals"""
    if DATABASE_URL:
        with SessionLocal() as db:
            # Get all cash outs
            cash_outs = db.query(CashOutDB).all()
            cash_out_data = []
            
            total_confirmed_cash_outs = 0
            for cash_out in cash_outs:
                player = db.query(PlayerDB).filter(PlayerDB.id == cash_out.player_id).first()
                player_name = player.name if player else "Unknown"
                
                if cash_out.confirmed:
                    total_confirmed_cash_outs += cash_out.amount
                
                cash_out_data.append({
                    "id": cash_out.id,
                    "player_name": player_name,
                    "amount": cash_out.amount,
                    "confirmed": cash_out.confirmed,
                    "timestamp": cash_out.timestamp.isoformat()
                })
            
            # Get current totals
            players = db.query(PlayerDB).all()
            total_player_balances = sum(p.total for p in players)
            
            confirmed_payments = db.query(PaymentDB).filter(PaymentDB.status == "confirmed").all()
            total_money_in = sum(p.amount - (DEALER_FEE if p.dealer_fee_applied else 0) for p in confirmed_payments)
            
            return {
                "cash_outs": cash_out_data,
                "total_confirmed_cash_outs": total_confirmed_cash_outs,
                "total_player_balances": total_player_balances,
                "total_money_in": total_money_in,
                "pot_should_be": total_money_in - total_confirmed_cash_outs,
                "pot_calculation_correct": abs((total_money_in - total_confirmed_cash_outs) - total_player_balances) < 0.01
            }
    else:
        return {"error": "Database not configured"}

@app.get("/api/cashouts/history")
async def get_cash_out_history():
    if DATABASE_URL:
        with SessionLocal() as db:
            confirmed = db.query(CashOutDB).filter(
                CashOutDB.confirmed == True
            ).order_by(CashOutDB.timestamp.desc()).all()
            
            result = []
            for cash_out in confirmed:
                # Get player name separately - NO JOIN
                player = db.query(PlayerDB).filter(PlayerDB.id == cash_out.player_id).first()
                player_name = player.name if player else "Unknown Player"
                
                result.append({
                    "id": cash_out.id,
                    "player_id": cash_out.player_id,
                    "player_name": player_name,
                    "amount": cash_out.amount,
                    "timestamp": cash_out.timestamp.isoformat(),
                    "reason": cash_out.reason,
                    "confirmed": cash_out.confirmed
                })
            
            return result
    else:
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

# BACKUP AND RESTORE ENDPOINTS

@app.get("/api/admin/backup")
def backup_game_data():
    """Export all game data as comprehensive JSON backup"""
    try:
        backup_data = {
            "backup_timestamp": datetime.now().isoformat(),
            "app_version": "1.0",
            "players": [],
            "payments": [],
            "cashouts": [],
            "game_stats": {}
        }
        
        if DATABASE_URL:
            with SessionLocal() as db:
                # Get players
                players = db.query(PlayerDB).all()
                backup_data["players"] = [{
                    "id": p.id,
                    "name": p.name,
                    "total": p.total,
                    "created_at": p.created_at.isoformat()
                } for p in players]
                
                # Get payments
                payments = db.query(PaymentDB).all()
                backup_data["payments"] = [{
                    "id": p.id,
                    "player_id": p.player_id,
                    "amount": p.amount,
                    "method": p.method,
                    "type": p.type,
                    "dealer_fee_applied": p.dealer_fee_applied,
                    "status": p.status,
                    "timestamp": p.timestamp.isoformat()
                } for p in payments]
                
                # Get cash outs
                cash_outs = db.query(CashOutDB).all()
                backup_data["cashouts"] = [{
                    "id": c.id,
                    "player_id": c.player_id,
                    "amount": c.amount,
                    "timestamp": c.timestamp.isoformat(),
                    "reason": c.reason,
                    "confirmed": c.confirmed
                } for c in cash_outs]
        else:
            # In-memory backup
            backup_data["players"] = list(players_db.values())
            backup_data["payments"] = []
            backup_data["cashouts"] = []
            
            # Extract payments from player data
            for player in players_db.values():
                if "payments" in player:
                    for payment in player["payments"]:
                        backup_data["payments"].append({
                            **payment,
                            "player_id": player["id"]
                        })
            
            # Extract cash outs
            for player_id, player_cash_outs in cash_outs_db.items():
                for cash_out in player_cash_outs:
                    backup_data["cashouts"].append(cash_out)
        
        # Calculate game statistics
        total_pot = sum(p["total"] for p in backup_data["players"])
        total_payments = sum(p["amount"] for p in backup_data["payments"])
        total_cash_outs = sum(c["amount"] for c in backup_data["cashouts"] if c["confirmed"])
        
        backup_data["game_stats"] = {
            "total_pot": total_pot,
            "total_payments": total_payments,
            "total_cash_outs": total_cash_outs,
            "player_count": len(backup_data["players"]),
            "payment_count": len(backup_data["payments"])
        }
        
        return backup_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {str(e)}")

@app.post("/api/admin/restore")
def restore_game_data(backup_data: dict):
    """Restore game data from backup"""
    try:
        if DATABASE_URL:
            with SessionLocal() as db:
                # Clear existing data first
                db.query(CashOutDB).delete()
                db.query(PaymentDB).delete()
                db.query(PlayerDB).delete()
                db.commit()
                
                # Restore players
                if "players" in backup_data:
                    for player_data in backup_data["players"]:
                        db_player = PlayerDB(
                            id=player_data["id"],
                            name=player_data["name"],
                            total=player_data.get("total", 0.0),
                            created_at=datetime.fromisoformat(player_data["created_at"].replace("Z", "+00:00")) if "created_at" in player_data else datetime.now()
                        )
                        db.add(db_player)
                    db.commit()
                
                # Restore payments
                if "payments" in backup_data:
                    for payment_data in backup_data["payments"]:
                        db_payment = PaymentDB(
                            id=payment_data["id"],
                            player_id=payment_data["player_id"],
                            amount=payment_data["amount"],
                            method=payment_data["method"],
                            type=payment_data["type"],
                            dealer_fee_applied=payment_data.get("dealer_fee_applied", False),
                            status=payment_data.get("status", "confirmed"),
                            timestamp=datetime.fromisoformat(payment_data["timestamp"].replace("Z", "+00:00")) if "timestamp" in payment_data else datetime.now()
                        )
                        db.add(db_payment)
                    db.commit()
                
                # Restore cash outs
                if "cashouts" in backup_data:
                    for cashout_data in backup_data["cashouts"]:
                        db_cashout = CashOutDB(
                            id=cashout_data["id"],
                            player_id=cashout_data["player_id"],
                            amount=cashout_data["amount"],
                            timestamp=datetime.fromisoformat(cashout_data["timestamp"].replace("Z", "+00:00")) if "timestamp" in cashout_data else datetime.now(),
                            reason=cashout_data.get("reason", "Player cashed out"),
                            confirmed=cashout_data.get("confirmed", False)
                        )
                        db.add(db_cashout)
                    db.commit()
        else:
            # In-memory restore
            players_db.clear()
            cash_outs_db.clear()
            
            if "players" in backup_data:
                for player_data in backup_data["players"]:
                    players_db[player_data["id"]] = player_data
        
        return {
            "success": True, 
            "message": f"Restored {len(backup_data.get('players', []))} players, {len(backup_data.get('payments', []))} payments, and {len(backup_data.get('cashouts', []))} cash outs",
            "restored_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)