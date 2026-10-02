import os
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from config import Config
from models import db, User, Listing, Exchange, CreditTransaction, Review, Message, MessageRequest, PaidService, PaidBooking, ServiceRequest, ServiceOffer
from datetime import datetime, timezone
from sqlalchemy import or_, and_
from flask_socketio import SocketIO, emit, join_room

# Initialize the Flask application
app = Flask(__name__)
app.config.from_object(Config)
socketio = SocketIO(app, cors_allowed_origins="*")
db.init_app(app)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'login' 
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- ROUTES ---

@app.context_processor
def inject_user():
    return dict(current_user=current_user)

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        ward = request.form.get('ward')
        
        user_exists = User.query.filter_by(email=email).first()
        if user_exists:
            return redirect(url_for('register'))
            
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(name=name, email=email, password=hashed_password, ward=ward)
        
        db.session.add(new_user)
        db.session.commit()
        
        login_user(new_user)
        return redirect(url_for('home'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('home'))
        else:
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    my_listings = Listing.query.filter_by(user_id=current_user.id).order_by(Listing.created_at.desc()).all()
    
    # 1. Skill Barter Exchanges (Credit-based)
    my_exchanges = Exchange.query.filter(
        (Exchange.helper_id == current_user.id) | (Exchange.requester_id == current_user.id)
    ).order_by(Exchange.created_at.desc()).all()
    
    exchange_data = []
    for ex in my_exchanges:
        lst = Listing.query.get(ex.listing_id)
        other_user_id = ex.requester_id if current_user.id == ex.helper_id else ex.helper_id
        other_user = User.query.get(other_user_id)
        role = "Helper" if current_user.id == ex.helper_id else "Requester"
        exchange_data.append({'exchange': ex, 'listing': lst, 'other_user': other_user, 'role': role})
        
    # 2. Credit Transactions (Credits Only)
    transactions = CreditTransaction.query.filter_by(user_id=current_user.id).order_by(CreditTransaction.created_at.desc()).limit(5).all()
    
    # 3. Unified Paid History (Bookings + Accepted Offers - Money Only)
    paid_history = []
    completed_bookings = PaidBooking.query.join(PaidService).filter(
        (PaidService.provider_id == current_user.id) | (PaidBooking.customer_id == current_user.id),
        PaidBooking.status == 'completed'
    ).all()

    for b in completed_bookings:
        svc = PaidService.query.get(b.service_id)
        is_provider = current_user.id == svc.provider_id
        paid_history.append({
            'title': svc.title,
            'other_user': User.query.get(b.customer_id if is_provider else svc.provider_id),
            'role': "Provider (Received Money)" if is_provider else "Client (Paid Money)",
            'amount': svc.price,
            'date': b.completed_at
        })
        
    completed_offers = ServiceOffer.query.join(ServiceRequest).filter(
        (ServiceOffer.helper_id == current_user.id) | (ServiceRequest.requester_id == current_user.id),
        ServiceOffer.status == 'completed'
    ).all()
    
    for o in completed_offers:
        req = ServiceRequest.query.get(o.request_id)
        is_helper = current_user.id == o.helper_id
        paid_history.append({
            'title': req.title,
            'other_user': User.query.get(req.requester_id if is_helper else o.helper_id),
            'role': "Helper (Received Money)" if is_helper else "Requester (Paid Money)",
            'amount': o.proposed_price,
            'date': o.created_at
        })
        
    paid_history.sort(key=lambda x: x['date'] if x['date'] else datetime.min, reverse=True)

    return render_template('dashboard.html', my_listings=my_listings, exchanges=exchange_data, transactions=transactions, paid_history=paid_history)

@app.route('/listing/<int:id>/request', methods=['POST'])
@login_required
def request_exchange(id):
    listing = Listing.query.get_or_404(id)
    active_exchange = Exchange.query.join(Listing).filter(
        db.or_(
            db.and_(Exchange.requester_id == current_user.id, Listing.user_id == listing.user_id),
            db.and_(Exchange.requester_id == listing.user_id, Listing.user_id == current_user.id)
        ),
        Exchange.status.in_(['pending', 'accepted'])
    ).first()
    
    if not active_exchange:
        helper_id = listing.user_id if listing.type == 'offer' else current_user.id
        req_id = current_user.id if listing.type == 'offer' else listing.user_id
        new_exchange = Exchange(listing_id=listing.id, helper_id=helper_id, requester_id=req_id, status='pending')
        db.session.add(new_exchange)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/exchange/<int:id>/complete', methods=['POST'])
@login_required
def complete_exchange(id):
    exchange = Exchange.query.get_or_404(id)
    listing = Listing.query.get(exchange.listing_id)
    
    if exchange.status != 'completed':
        helper = User.query.get(exchange.helper_id)
        requester = User.query.get(exchange.requester_id)
        
        if requester.credits >= listing.credits:
            requester.credits -= listing.credits
            helper.credits += listing.credits
            exchange.status = 'completed'
            exchange.completed_at = datetime.now(timezone.utc)
            listing.exchange_count += 1
            
            db.session.add_all([
                CreditTransaction(user_id=requester.id, amount=-listing.credits, reason=f"Received help for: {listing.title}"),
                CreditTransaction(user_id=helper.id, amount=listing.credits, reason=f"Provided help for: {listing.title}")
            ])
            db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/exchange/<int:id>/review', methods=['GET', 'POST'])
@login_required
def submit_review(id):
    exchange = Exchange.query.get_or_404(id)
    if exchange.status != 'completed' or current_user.id not in [exchange.helper_id, exchange.requester_id]:
        return redirect(url_for('dashboard'))
        
    reviewee_id = exchange.requester_id if current_user.id == exchange.helper_id else exchange.helper_id
    if Review.query.filter_by(exchange_id=exchange.id, reviewer_id=current_user.id).first():
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        db.session.add(Review(
            exchange_id=exchange.id, reviewer_id=current_user.id, reviewee_id=reviewee_id,
            rating=int(request.form.get('rating')), comment=request.form.get('comment')
        ))
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('submit_review.html', exchange=exchange, listing=Listing.query.get(exchange.listing_id), reviewee=User.query.get(reviewee_id))

@app.route('/board')
def board():
    query = Listing.query.filter_by(status='open')
    if request.args.get('type'): query = query.filter_by(type=request.args.get('type'))
    if request.args.get('category'): query = query.filter_by(category=request.args.get('category'))
    return render_template('board.html', listings=query.order_by(Listing.created_at.desc()).all())

@app.route('/listing/<int:id>')
def view_listing(id):
    listing = Listing.query.get_or_404(id)
    return render_template('listing.html', listing=listing, author=User.query.get(listing.user_id))

@app.route('/profile/<int:id>')
def profile(id):
    profile_user = User.query.get_or_404(id)
    listings = Listing.query.filter_by(user_id=profile_user.id, status='open').order_by(Listing.created_at.desc()).all()
    reviews = [{'review': r, 'reviewer': User.query.get(r.reviewer_id)} for r in Review.query.filter_by(reviewee_id=id).order_by(Review.created_at.desc()).all()]
    return render_template('profile.html', profile_user=profile_user, listings=listings, reviews=reviews)

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        current_user.name = request.form.get('name')
        current_user.ward = request.form.get('ward')
        current_user.bio = request.form.get('bio')
        current_user.avatar_style = request.form.get('avatar_style', 'initials')
        custom_seed = request.form.get('avatar_seed')
        current_user.avatar_seed = custom_seed.strip() if custom_seed and custom_seed.strip() else current_user.name
        db.session.commit()
        return redirect(url_for('profile', id=current_user.id))
    return render_template('edit_profile.html')

@app.route('/leaderboard')
def leaderboard():
    return render_template('leaderboard.html', top_users=User.query.order_by(User.credits.desc()).limit(10).all())

@app.route('/messages')
@login_required
def messages_inbox():
    pending = MessageRequest.query.filter_by(receiver_id=current_user.id, status='pending').all()
    for req in pending: req.sender = User.query.get(req.sender_id)
    accepted = MessageRequest.query.filter(and_(or_(MessageRequest.sender_id == current_user.id, MessageRequest.receiver_id == current_user.id), MessageRequest.status == 'accepted')).all()
    contacts = [User.query.get(c.receiver_id if c.sender_id == current_user.id else c.sender_id) for c in accepted]
    return render_template('messages.html', contacts=contacts, pending_requests=pending)

@app.route('/messages/request/<int:id>/<action>')
@login_required
def handle_message_request(id, action):
    req = MessageRequest.query.get_or_404(id)
    if req.receiver_id == current_user.id and req.status == 'pending':
        req.status = 'accepted' if action == 'accept' else 'declined'
        db.session.commit()
    return redirect(url_for('messages_inbox'))

@app.route('/messages/chat/<int:user_id>', methods=['GET', 'POST'])
@login_required
def chat(user_id):
    if user_id == current_user.id: return redirect(url_for('dashboard'))
    conn = MessageRequest.query.filter(or_(and_(MessageRequest.sender_id == current_user.id, MessageRequest.receiver_id == user_id), and_(MessageRequest.sender_id == user_id, MessageRequest.receiver_id == current_user.id))).first()
    
    if request.method == 'POST':
        if not conn:
            db.session.add(MessageRequest(sender_id=current_user.id, receiver_id=user_id))
        elif conn.status == 'accepted' and request.form.get('body').strip():
            db.session.add(Message(sender_id=current_user.id, receiver_id=user_id, body=request.form.get('body')))
        db.session.commit()
        return redirect(url_for('chat', user_id=user_id))
        
    messages = []
    if conn and conn.status == 'accepted':
        messages = Message.query.filter(or_(and_(Message.sender_id == current_user.id, Message.receiver_id == user_id), and_(Message.sender_id == user_id, Message.receiver_id == current_user.id))).order_by(Message.sent_at.asc()).all()
        for msg in messages:
            if msg.receiver_id == current_user.id and not msg.is_read: msg.is_read = True
        db.session.commit()
    return render_template('send_message.html', other_user=User.query.get_or_404(user_id), connection=conn, messages=messages)

@app.route('/post', methods=['GET', 'POST'])
@login_required
def post_listing():
    if request.method == 'POST':
        db.session.add(Listing(
            user_id=current_user.id, type=request.form.get('type'), category=request.form.get('category'),
            title=request.form.get('title'), description=request.form.get('description'),
            credits=int(request.form.get('credits')), ward=request.form.get('ward')
        ))
        db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('post_listing.html')

# --- PAID SERVICES & REQUESTS ---

@app.route('/paid-services')
def paid_services():
    # REMOVED the strict status filter. This guarantees all services will show!
    services = PaidService.query.order_by(PaidService.created_at.desc()).all()
    for service in services: service.provider = User.query.get(service.provider_id)
    return render_template('paid_services.html', services=services)

@app.route('/paid-services/post', methods=['GET', 'POST'])
@login_required
def post_paid_service():
    if request.method == 'POST':
        db.session.add(PaidService(
            provider_id=current_user.id, title=request.form.get('title'), description=request.form.get('description'),
            category=request.form.get('category'), price=float(request.form.get('price')), duration=request.form.get('duration'),
            location=request.form.get('location'), availability=request.form.get('availability'), status='active'
        ))
        db.session.commit()
        return redirect(url_for('paid_services'))
    return render_template('post_paid_service.html')

@app.route('/paid-services/<int:id>')
def view_paid_service(id):
    svc = PaidService.query.get_or_404(id)
    return render_template('view_paid_service.html', service=svc, provider=User.query.get(svc.provider_id))

@app.route('/paid-services/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_paid_service(id):
    service = PaidService.query.get_or_404(id)
    if service.provider_id != current_user.id:
        return redirect(url_for('view_paid_service', id=id))
        
    if request.method == 'POST':
        service.title = request.form.get('title')
        service.description = request.form.get('description')
        service.category = request.form.get('category')
        service.price = float(request.form.get('price'))
        service.duration = request.form.get('duration')
        service.location = request.form.get('location')
        service.availability = request.form.get('availability')
        db.session.commit()
        flash("Paid service updated successfully!", "success")
        return redirect(url_for('view_paid_service', id=service.id))
    return render_template('edit_paid_service.html', service=service)

@app.route('/paid-services/<int:id>/book', methods=['GET', 'POST'])
@login_required
def book_service(id):
    service = PaidService.query.get_or_404(id)
    if service.provider_id == current_user.id: return redirect(url_for('view_paid_service', id=service.id))
    if request.method == 'POST':
        db.session.add(PaidBooking(service_id=service.id, customer_id=current_user.id, booking_date=request.form.get('booking_date'), notes=request.form.get('notes')))
        db.session.commit()
        return redirect(url_for('my_bookings'))
    return render_template('book_service.html', service=service)

@app.route('/my-bookings')
@login_required
def my_bookings():
    incoming = PaidBooking.query.join(PaidService).filter(PaidService.provider_id == current_user.id).order_by(PaidBooking.created_at.desc()).all()
    prov_b = [{'booking': b, 'service': PaidService.query.get(b.service_id), 'customer': User.query.get(b.customer_id)} for b in incoming]
    outgoing = PaidBooking.query.filter_by(customer_id=current_user.id).order_by(PaidBooking.created_at.desc()).all()
    cust_b = [{'booking': b, 'service': PaidService.query.get(b.service_id), 'provider': User.query.get(PaidService.query.get(b.service_id).provider_id)} for b in outgoing]
    return render_template('my_bookings.html', provider_bookings=prov_b, customer_bookings=cust_b)

@app.route('/bookings/<int:id>/<action>')
@login_required
def handle_booking(id, action):
    booking = PaidBooking.query.get_or_404(id)
    service = PaidService.query.get(booking.service_id)
    if current_user.id == service.provider_id:
        if action == 'accept' and booking.status == 'pending': booking.status = 'accepted'
        elif action in ['complete', 'payment_done'] and booking.status == 'accepted':
            booking.status = 'completed'
            booking.completed_at = datetime.now(timezone.utc)
            flash("Money transaction recorded.", "success")
        elif action == 'cancel' and booking.status in ['pending', 'accepted']: booking.status = 'cancelled'
    elif current_user.id == booking.customer_id and action == 'cancel' and booking.status in ['pending', 'accepted']:
        booking.status = 'cancelled'
    db.session.commit()
    return redirect(url_for('my_bookings'))

@app.route('/service-requests')
def service_requests():
    # REMOVED strict status filters here as well. Guarantees visibility!
    requests = ServiceRequest.query.order_by(ServiceRequest.created_at.desc()).all()
    return render_template('service_requests.html', requests=requests)

@app.route('/request-service', methods=['GET', 'POST'])
@login_required
def request_service():
    if request.method == 'POST':
        db.session.add(ServiceRequest(
            requester_id=current_user.id, category=request.form.get('category'), title=request.form.get('title'),
            description=request.form.get('description'), budget=float(request.form.get('budget')), payment_type=request.form.get('payment_type'),
            location=request.form.get('location'), preferred_date=request.form.get('preferred_date'), preferred_time=request.form.get('preferred_time'),
            urgency=request.form.get('urgency'), additional_details=request.form.get('additional_details'), contact_preference=request.form.get('contact_preference'), status='open'
        ))
        db.session.commit()
        return redirect(url_for('service_requests'))
    return render_template('request_service.html')

@app.route('/service-request/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_service_request(id):
    req = ServiceRequest.query.get_or_404(id)
    if req.requester_id != current_user.id:
        return redirect(url_for('view_service_request', id=id))
        
    if request.method == 'POST':
        req.title = request.form.get('title')
        req.description = request.form.get('description')
        req.category = request.form.get('category')
        req.budget = float(request.form.get('budget'))
        req.payment_type = request.form.get('payment_type')
        req.location = request.form.get('location')
        req.preferred_date = request.form.get('preferred_date')
        req.preferred_time = request.form.get('preferred_time')
        db.session.commit()
        flash("Service request updated successfully!", "success")
        return redirect(url_for('view_service_request', id=req.id))
    return render_template('edit_service_request.html', req=req)

@app.route('/service-request/<int:id>')
def view_service_request(id):
    req = ServiceRequest.query.get_or_404(id)
    existing_offer = ServiceOffer.query.filter_by(request_id=id, helper_id=current_user.id).first() if current_user.is_authenticated else None
    return render_template('view_service_request.html', req=req, existing_offer=existing_offer)

@app.route('/my-service-requests')
@login_required
def my_service_requests():
    return render_template('my_service_requests.html', requests=ServiceRequest.query.filter_by(requester_id=current_user.id).order_by(ServiceRequest.created_at.desc()).all())

@app.route('/service-request/<int:id>/cancel')
@login_required
def cancel_service_request(id):
    req = ServiceRequest.query.get_or_404(id)
    if req.requester_id == current_user.id and req.status == 'open':
        req.status = 'cancelled'
        for offer in ServiceOffer.query.filter_by(request_id=id, status='pending').all(): offer.status = 'withdrawn'
        db.session.commit()
    return redirect(url_for('my_service_requests'))

@app.route('/service-request/<int:id>/offer', methods=['POST'])
@login_required
def offer_service(id):
    req = ServiceRequest.query.get_or_404(id)
    if req.requester_id == current_user.id or req.status != 'open': return redirect(url_for('view_service_request', id=id))
    db.session.add(ServiceOffer(request_id=id, helper_id=current_user.id, message=request.form.get('message'), proposed_price=float(request.form.get('proposed_price') or req.budget), availability=request.form.get('availability')))
    db.session.commit()
    return redirect(url_for('view_service_request', id=id))

@app.route('/service-offer/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_service_offer(id):
    offer = ServiceOffer.query.get_or_404(id)
    if offer.helper_id != current_user.id or offer.status != 'pending':
        return redirect(url_for('view_service_request', id=offer.request_id))
        
    if request.method == 'POST':
        offer.message = request.form.get('message')
        offer.proposed_price = float(request.form.get('proposed_price'))
        offer.availability = request.form.get('availability')
        db.session.commit()
        flash("Offer updated successfully!", "success")
        return redirect(url_for('view_service_request', id=offer.request_id))
    return render_template('edit_service_offer.html', offer=offer)

@app.route('/service-request/<int:id>/offers')
@login_required
def view_request_offers(id):
    req = ServiceRequest.query.get_or_404(id)
    if req.requester_id != current_user.id: return redirect(url_for('home'))
    return render_template('view_request_offers.html', req=req, offers=ServiceOffer.query.filter_by(request_id=id).order_by(ServiceOffer.created_at.desc()).all())

@app.route('/service-offer/<int:id>/<action>')
@login_required
def handle_service_offer(id, action):
    offer = ServiceOffer.query.get_or_404(id)
    req = ServiceRequest.query.get(offer.request_id)
    
    if current_user.id == req.requester_id:
        if action == 'accept' and offer.status == 'pending':
            offer.status = 'accepted'
            req.status = 'accepted'
            for other in ServiceOffer.query.filter(ServiceOffer.request_id == req.id, ServiceOffer.id != offer.id).all():
                if other.status == 'pending': other.status = 'rejected'
            
            existing = MessageRequest.query.filter(or_(and_(MessageRequest.sender_id == current_user.id, MessageRequest.receiver_id == offer.helper_id), and_(MessageRequest.sender_id == offer.helper_id, MessageRequest.receiver_id == current_user.id))).first()
            if not existing: db.session.add(MessageRequest(sender_id=current_user.id, receiver_id=offer.helper_id, status='accepted'))
            elif existing.status != 'accepted': existing.status = 'accepted'
        elif action == 'reject' and offer.status == 'pending': offer.status = 'rejected'
        
    elif current_user.id == offer.helper_id:
        if action == 'payment_done' and offer.status == 'accepted':
            offer.status = 'completed'
            req.status = 'completed'
            flash(f"Money transaction of ₹{offer.proposed_price} confirmed!", "success")
            
    db.session.commit()
    return redirect(url_for('view_request_offers', id=req.id))

@app.route('/listing/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_listing(id):
    listing = Listing.query.get_or_404(id)
    if listing.user_id != current_user.id: return redirect(url_for('view_listing', id=id))
    if request.method == 'POST':
        listing.type = request.form.get('type')
        listing.category = request.form.get('category')
        listing.title = request.form.get('title')
        listing.description = request.form.get('description')
        listing.credits = int(request.form.get('credits'))
        listing.ward = request.form.get('ward')
        db.session.commit()
        return redirect(url_for('view_listing', id=listing.id))
    return render_template('edit_listing.html', listing=listing)

@app.route('/listing/<int:id>/delete')
@login_required
def delete_listing(id):
    listing = Listing.query.get_or_404(id)
    if listing.user_id == current_user.id:
        db.session.delete(listing)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/notifications')
@login_required
def notifications():
    data = []
    for ex in Exchange.query.filter_by(status='pending').all():
        lst = Listing.query.get(ex.listing_id)
        if lst and lst.user_id == current_user.id:
            data.append({'id': ex.id, 'status': ex.status, 'listing': lst, 'requester': User.query.get(getattr(ex, 'requester_id', getattr(ex, 'user_id', None)))})
    return render_template('notifications.html', pending_exchanges=data)

@app.route('/exchange/<int:id>/<action>')
@login_required
def handle_exchange(id, action):
    exchange = Exchange.query.get_or_404(id)
    lst = Listing.query.get(exchange.listing_id)
    if lst and lst.user_id == current_user.id:
        if action == 'accept': exchange.status = 'accepted'
        elif action == 'reject': exchange.status = 'rejected'
        db.session.commit()
    return redirect(url_for('notifications'))

@app.route('/report/<int:id>')
@login_required
def report_user(id):
    return redirect(url_for('dashboard'))

@socketio.on('join')
def on_join(data):
    join_room(data['room'])

@socketio.on('send_message')
def handle_message(data):
    db.session.add(Message(sender_id=current_user.id, receiver_id=data['receiver_id'], body=data['body']))
    db.session.commit()
    emit('receive_message', {'body': data['body'], 'sender_id': current_user.id, 'sent_at': datetime.now(timezone.utc).strftime('%H:%M')}, room=f"chat_{min(current_user.id, data['receiver_id'])}_{max(current_user.id, data['receiver_id'])}")

@app.route('/admin')
@login_required
def admin_panel():
    if current_user.role != 'admin': return redirect(url_for('home'))
    stats = {'users': User.query.count(), 'listings': Listing.query.count(), 'exchanges': Exchange.query.count(), 'paid_services': PaidService.query.count()}
    return render_template('admin.html', stats=stats, users=User.query.order_by(User.id.asc()).all())

if __name__ == '__main__':
    socketio.run(app, debug=True)