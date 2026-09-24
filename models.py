from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=True) # Nullable for Google Sign-in
    ward = db.Column(db.String(100), nullable=True)
    credits = db.Column(db.Integer, default=10)
    bio = db.Column(db.Text, nullable=True)
    photo = db.Column(db.String(255), nullable=True)
    avatar_seed = db.Column(db.String(100), nullable=True)
    avatar_style = db.Column(db.String(50), default='bottts')
    role = db.Column(db.String(20), default='user') # 'user' or 'admin'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Listing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False) # 'offer' or 'request'
    category = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)
    credits = db.Column(db.Integer, nullable=False)
    ward = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='open') # 'open' or 'closed'
    exchange_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Exchange(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey('listing.id'), nullable=False)
    helper_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending') # 'pending' or 'completed'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    body = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

class MessageRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending') # 'pending', 'accepted', 'declined'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exchange_id = db.Column(db.Integer, db.ForeignKey('exchange.id'), nullable=False)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reviewee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False) # 1 to 5
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class CreditTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False) # Positive (earned) or negative (spent)
    reason = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PaidService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(100), nullable=False)
    availability = db.Column(db.String(150), nullable=False)
    duration = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class PaidBooking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('paid_service.id'), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    booking_date = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='pending') # 'pending', 'accepted', 'completed', 'cancelled'
    payment_status = db.Column(db.String(20), default='pending')
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)