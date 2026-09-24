from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from config import Config
from models import db, User, Listing, Exchange, CreditTransaction, Review, Message, MessageRequest, PaidService, PaidBooking
from datetime import datetime
from sqlalchemy import or_, and_

# Initialize the Flask application
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'login' 
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Create tables


# --- ROUTES ---

@app.context_processor
def inject_user():
    # This ensures current_user is available in all HTML templates
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
        
        # Check if email already exists
        user_exists = User.query.filter_by(email=email).first()
        if user_exists:
            # We will replace this simple print with toast notifications later
            print("Email already exists!")
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
            print("Invalid email or password")
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Fetch user's listings
    my_listings = Listing.query.filter_by(user_id=current_user.id).order_by(Listing.created_at.desc()).all()
    
    # Fetch user's exchanges (where they are either the helper OR the requester)
    my_exchanges = Exchange.query.filter(
        (Exchange.helper_id == current_user.id) | (Exchange.requester_id == current_user.id)
    ).order_by(Exchange.created_at.desc()).all()
    
    # Bundle the exchange data nicely for the HTML template
    exchange_data = []
    for ex in my_exchanges:
        lst = Listing.query.get(ex.listing_id)
        other_user_id = ex.requester_id if current_user.id == ex.helper_id else ex.helper_id
        other_user = User.query.get(other_user_id)
        role = "Helper" if current_user.id == ex.helper_id else "Requester"
        exchange_data.append({'exchange': ex, 'listing': lst, 'other_user': other_user, 'role': role})
        
    # Fetch recent credit transactions
    transactions = CreditTransaction.query.filter_by(user_id=current_user.id).order_by(CreditTransaction.created_at.desc()).limit(5).all()
    
    return render_template('dashboard.html', my_listings=my_listings, exchanges=exchange_data, transactions=transactions)

@app.route('/listing/<int:id>/request', methods=['POST'])
@login_required
def request_exchange(id):
    listing = Listing.query.get_or_404(id)
    
    # Determine roles based on listing type
    if listing.type == 'offer':
        helper_id = listing.user_id
        requester_id = current_user.id
    else:
        helper_id = current_user.id
        requester_id = listing.user_id
        
    new_exchange = Exchange(
        listing_id=listing.id,
        helper_id=helper_id,
        requester_id=requester_id,
        status='pending'
    )
    db.session.add(new_exchange)
    db.session.commit()
    
    return redirect(url_for('dashboard'))

@app.route('/exchange/<int:id>/complete', methods=['POST'])
@login_required
def complete_exchange(id):
    exchange = Exchange.query.get_or_404(id)
    listing = Listing.query.get(exchange.listing_id)
    
    if exchange.status == 'completed':
        return redirect(url_for('dashboard'))
        
    helper = User.query.get(exchange.helper_id)
    requester = User.query.get(exchange.requester_id)
    
    # Atomic credit transfer logic
    if requester.credits >= listing.credits:
        requester.credits -= listing.credits
        helper.credits += listing.credits
        
        exchange.status = 'completed'
        exchange.completed_at = datetime.utcnow()
        listing.exchange_count += 1
        
        # Record history for both users
        tx_spend = CreditTransaction(user_id=requester.id, amount=-listing.credits, reason=f"Received help for: {listing.title}")
        tx_earn = CreditTransaction(user_id=helper.id, amount=listing.credits, reason=f"Provided help for: {listing.title}")
        
        db.session.add_all([tx_spend, tx_earn])
        db.session.commit()
        
    return redirect(url_for('dashboard'))

@app.route('/exchange/<int:id>/review', methods=['GET', 'POST'])
@login_required
def submit_review(id):
    exchange = Exchange.query.get_or_404(id)
    listing = Listing.query.get(exchange.listing_id)
    
    # Security: Ensure only participants can review, and only after completion
    if exchange.status != 'completed' or current_user.id not in [exchange.helper_id, exchange.requester_id]:
        return redirect(url_for('dashboard'))
        
    # Determine who is being reviewed
    reviewee_id = exchange.requester_id if current_user.id == exchange.helper_id else exchange.helper_id
    reviewee = User.query.get(reviewee_id)
    
    # Check if a review already exists from this user for this exchange
    existing_review = Review.query.filter_by(exchange_id=exchange.id, reviewer_id=current_user.id).first()
    if existing_review:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        rating = int(request.form.get('rating'))
        comment = request.form.get('comment')
        
        new_review = Review(
            exchange_id=exchange.id,
            reviewer_id=current_user.id,
            reviewee_id=reviewee_id,
            rating=rating,
            comment=comment
        )
        db.session.add(new_review)
        db.session.commit()
        return redirect(url_for('dashboard'))
        
    return render_template('submit_review.html', exchange=exchange, listing=listing, reviewee=reviewee)

@app.route('/board')
def board():
    # Get filter arguments from the URL if they exist
    type_filter = request.args.get('type')
    category_filter = request.args.get('category')
    
    # Start a database query for open listings
    query = Listing.query.filter_by(status='open')
    
    # Apply filters if the user selected them
    if type_filter:
        query = query.filter_by(type=type_filter)
    if category_filter:
        query = query.filter_by(category=category_filter)
        
    # Get the results, ordered by newest first
    listings = query.order_by(Listing.created_at.desc()).all()
    
    return render_template('board.html', listings=listings)

@app.route('/listing/<int:id>')
def view_listing(id):
    # Fetch the listing by its ID, return 404 if not found
    listing = Listing.query.get_or_404(id)
    # Fetch the user who posted this listing
    author = User.query.get(listing.user_id)
    
    return render_template('listing.html', listing=listing, author=author)

@app.route('/profile/<int:id>')
def profile(id):
    profile_user = User.query.get_or_404(id)
    user_listings = Listing.query.filter_by(user_id=profile_user.id, status='open').order_by(Listing.created_at.desc()).all()
    
    # Fetch all reviews where this user is the reviewee
    raw_reviews = Review.query.filter_by(reviewee_id=id).order_by(Review.created_at.desc()).all()
    
    # Bundle reviews with the reviewer's info
    reviews = []
    for r in raw_reviews:
        reviewer = User.query.get(r.reviewer_id)
        reviews.append({'review': r, 'reviewer': reviewer})
    
    return render_template('profile.html', profile_user=profile_user, listings=user_listings, reviews=reviews)

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        # Update the user object with new form data
        current_user.name = request.form.get('name')
        current_user.ward = request.form.get('ward')
        current_user.bio = request.form.get('bio')
        current_user.avatar_style = request.form.get('avatar_style')
        
        # If they left the custom seed blank, use their name as the seed
        custom_seed = request.form.get('avatar_seed')
        if custom_seed and custom_seed.strip():
            current_user.avatar_seed = custom_seed.strip()
        else:
            current_user.avatar_seed = current_user.name
            
        # Save changes to database
        db.session.commit()
        
        return redirect(url_for('profile', id=current_user.id))
        
    return render_template('edit_profile.html')

@app.route('/leaderboard')
def leaderboard():
    # Fetch the top 10 users with the most credits
    top_users = User.query.order_by(User.credits.desc()).limit(10).all()
    return render_template('leaderboard.html', top_users=top_users)

@app.route('/messages')
@login_required
def messages_inbox():
    # Fetch pending requests where the current user is the receiver
    pending_requests = MessageRequest.query.filter_by(receiver_id=current_user.id, status='pending').all()
    
    # Attach the sender object to each request so the HTML can display their name/avatar
    for req in pending_requests:
        req.sender = User.query.get(req.sender_id)
        
    # Fetch accepted connections to populate the inbox list
    accepted_connections = MessageRequest.query.filter(
        and_(
            or_(MessageRequest.sender_id == current_user.id, MessageRequest.receiver_id == current_user.id),
            MessageRequest.status == 'accepted'
        )
    ).all()
    
    contacts = []
    for conn in accepted_connections:
        other_user_id = conn.receiver_id if conn.sender_id == current_user.id else conn.sender_id
        contacts.append(User.query.get(other_user_id))
        
    return render_template('messages.html', contacts=contacts, pending_requests=pending_requests)

@app.route('/messages/request/<int:id>/<action>')
@login_required
def handle_message_request(id, action):
    req = MessageRequest.query.get_or_404(id)
    # Ensure only the receiver can accept/decline
    if req.receiver_id == current_user.id and req.status == 'pending':
        req.status = 'accepted' if action == 'accept' else 'declined'
        db.session.commit()
    return redirect(url_for('messages_inbox'))

@app.route('/messages/chat/<int:user_id>', methods=['GET', 'POST'])
@login_required
def chat(user_id):
    if user_id == current_user.id:
        return redirect(url_for('dashboard'))
        
    other_user = User.query.get_or_404(user_id)
    
    # Find existing connection regardless of who sent it
    connection = MessageRequest.query.filter(
        or_(
            and_(MessageRequest.sender_id == current_user.id, MessageRequest.receiver_id == user_id),
            and_(MessageRequest.sender_id == user_id, MessageRequest.receiver_id == current_user.id)
        )
    ).first()
    
    if request.method == 'POST':
        if not connection:
            new_req = MessageRequest(sender_id=current_user.id, receiver_id=user_id)
            db.session.add(new_req)
            db.session.commit()
        elif connection.status == 'accepted':
            body = request.form.get('body')
            if body.strip():
                new_msg = Message(sender_id=current_user.id, receiver_id=user_id, body=body)
                db.session.add(new_msg)
                db.session.commit()
        return redirect(url_for('chat', user_id=user_id))
        
    messages = []
    if connection and connection.status == 'accepted':
        messages = Message.query.filter(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == user_id),
                and_(Message.sender_id == user_id, Message.receiver_id == current_user.id)
            )
        ).order_by(Message.sent_at.asc()).all()
        
        # Mark incoming messages as read
        for msg in messages:
            if msg.receiver_id == current_user.id and not msg.is_read:
                msg.is_read = True
        db.session.commit()
        
    return render_template('send_message.html', other_user=other_user, connection=connection, messages=messages)

@app.route('/post', methods=['GET', 'POST'])
@login_required
def post_listing():
    if request.method == 'POST':
        type = request.form.get('type')
        category = request.form.get('category')
        title = request.form.get('title')
        description = request.form.get('description')
        credits = int(request.form.get('credits'))
        ward = request.form.get('ward')
        
        new_listing = Listing(
            user_id=current_user.id,
            type=type,
            category=category,
            title=title,
            description=description,
            credits=credits,
            ward=ward
        )
        
        db.session.add(new_listing)
        db.session.commit()
        
        return redirect(url_for('dashboard'))
        
    return render_template('post_listing.html')

@app.route('/paid-services')
def paid_services():
    services = PaidService.query.filter_by(status='active').order_by(PaidService.created_at.desc()).all()
    # Attach provider info so we can show their avatar
    for service in services:
        service.provider = User.query.get(service.provider_id)
    return render_template('paid_services.html', services=services)

@app.route('/paid-services/post', methods=['GET', 'POST'])
@login_required
def post_paid_service():
    if request.method == 'POST':
        new_service = PaidService(
            provider_id=current_user.id,
            title=request.form.get('title'),
            description=request.form.get('description'),
            category=request.form.get('category'),
            price=float(request.form.get('price')),
            duration=request.form.get('duration'),
            location=request.form.get('location'),
            availability=request.form.get('availability')
        )
        db.session.add(new_service)
        db.session.commit()
        return redirect(url_for('paid_services'))
        
    return render_template('post_paid_service.html')

@app.route('/paid-services/<int:id>')
def view_paid_service(id):
    service = PaidService.query.get_or_404(id)
    provider = User.query.get(service.provider_id)
    return render_template('view_paid_service.html', service=service, provider=provider)

@app.route('/paid-services/<int:id>/book', methods=['GET', 'POST'])
@login_required
def book_service(id):
    service = PaidService.query.get_or_404(id)
    
    # Security: Users cannot book their own services
    if service.provider_id == current_user.id:
        return redirect(url_for('view_paid_service', id=service.id))
        
    if request.method == 'POST':
        new_booking = PaidBooking(
            service_id=service.id,
            customer_id=current_user.id,
            booking_date=request.form.get('booking_date'),
            notes=request.form.get('notes')
        )
        db.session.add(new_booking)
        db.session.commit()
        return redirect(url_for('my_bookings'))
    return render_template('book_service.html', service=service)

@app.route('/my-bookings')
@login_required
def my_bookings():
    # Fetch bookings where the current user is the provider (Incoming)
    incoming = PaidBooking.query.join(PaidService).filter(PaidService.provider_id == current_user.id).order_by(PaidBooking.created_at.desc()).all()
    provider_bookings = []
    for b in incoming:
        service = PaidService.query.get(b.service_id)
        customer = User.query.get(b.customer_id)
        provider_bookings.append({'booking': b, 'service': service, 'customer': customer})
        
    # Fetch bookings where the current user is the customer (Outgoing)
    outgoing = PaidBooking.query.filter_by(customer_id=current_user.id).order_by(PaidBooking.created_at.desc()).all()
    customer_bookings = []
    for b in outgoing:
        service = PaidService.query.get(b.service_id)
        provider = User.query.get(service.provider_id)
        customer_bookings.append({'booking': b, 'service': service, 'provider': provider})
        
    return render_template('my_bookings.html', provider_bookings=provider_bookings, customer_bookings=customer_bookings)

@app.route('/bookings/<int:id>/<action>')
@login_required
def handle_booking(id, action):
    booking = PaidBooking.query.get_or_404(id)
    service = PaidService.query.get(booking.service_id)
    
    # Provider actions
    if current_user.id == service.provider_id:
        if action == 'accept' and booking.status == 'pending':
            booking.status = 'accepted'
        elif action == 'complete' and booking.status == 'accepted':
            booking.status = 'completed'
            booking.completed_at = datetime.utcnow()
        elif action == 'cancel' and booking.status in ['pending', 'accepted']:
            booking.status = 'cancelled'
            
    # Customer actions
    elif current_user.id == booking.customer_id:
        if action == 'cancel' and booking.status in ['pending', 'accepted']:
            booking.status = 'cancelled'
            
    db.session.commit()
    return redirect(url_for('my_bookings'))

@app.route('/admin')
@login_required
def admin_panel():
    # Security: Redirect non-admins back to home
    if current_user.role != 'admin':
        return redirect(url_for('home'))
        
    stats = {
        'users': User.query.count(),
        'listings': Listing.query.count(),
        'exchanges': Exchange.query.count(),
        'paid_services': PaidService.query.count()
    }
    
    users = User.query.order_by(User.id.asc()).all()
    
    return render_template('admin.html', stats=stats, users=users)

# Run the app locally
if __name__ == '__main__':
    app.run(debug=True)