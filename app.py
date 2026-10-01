import os
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from config import Config
from datetime import datetime
from flask_socketio import SocketIO, emit, join_room

# Initialize Flask
app = Flask(__name__)
app.config.from_object(Config)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- INITIALIZE FIREBASE ---
cred = credentials.Certificate("firebase-credentials.json")
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'login' 
login_manager.init_app(app)

# Helper User class for Flask-Login compatibility with Firestore
class UserClass:
    def __init__(self, user_id, data):
        self.id = user_id
        self.name = data.get('name')
        self.email = data.get('email')
        self.password = data.get('password')
        self.ward = data.get('ward')
        self.bio = data.get('bio', '')
        self.credits = data.get('credits', 10)
        self.avatar_style = data.get('avatar_style', 'initials')
        self.avatar_seed = data.get('avatar_seed', '')
        self.created_at = data.get('created_at', datetime.utcnow())
        self.role = data.get('role', 'user')

    def get_id(self):
        return str(self.id)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

@login_manager.user_loader
def load_user(user_id):
    user_doc = db.collection('users').document(str(user_id)).get()
    if user_doc.exists:
        return UserClass(user_doc.id, user_doc.to_dict())
    return None

@app.context_processor
def inject_user():
    return dict(current_user=current_user)

# --- ROUTES ---

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
        
        existing_user = db.collection('users').where('email', '==', email).limit(1).get()
        if list(existing_user):
            return redirect(url_for('register'))
            
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        user_data = {
            'name': name,
            'email': email,
            'password': hashed_password,
            'ward': ward,
            'credits': 10,
            'avatar_style': 'initials',
            'avatar_seed': name,
            'created_at': datetime.utcnow(),
            'role': 'user'
        }
        
        _, user_ref = db.collection('users').add(user_data)
        new_user = UserClass(user_ref.id, user_data)
        
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
        
        users_ref = db.collection('users').where('email', '==', email).limit(1).get()
        users_list = list(users_ref)
        
        if users_list:
            user_doc = users_list[0]
            user_data = user_doc.to_dict()
            if check_password_hash(user_data['password'], password):
                user_obj = UserClass(user_doc.id, user_data)
                login_user(user_obj)
                return redirect(url_for('home'))
                
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
    listings_ref = db.collection('listings').where('user_id', '==', current_user.id).stream()
    my_listings = [{'id': doc.id, **doc.to_dict()} for doc in listings_ref]
    return render_template('dashboard.html', my_listings=my_listings, exchanges=[], transactions=[])

@app.route('/board')
def board():
    listings_ref = db.collection('listings').where('status', '==', 'open').stream()
    listings = [{'id': doc.id, **doc.to_dict()} for doc in listings_ref]
    return render_template('board.html', listings=listings)

@app.route('/listing/<id>')
def view_listing(id):
    listing_doc = db.collection('listings').document(id).get()
    if not listing_doc.exists:
        return redirect(url_for('board'))
    listing = {'id': listing_doc.id, **listing_doc.to_dict()}
    
    author_doc = db.collection('users').document(listing['user_id']).get()
    author = author_doc.to_dict() if author_doc.exists else {'name': 'Unknown', 'ward': 'N/A'}
    author['id'] = author_doc.id
    
    return render_template('listing.html', listing=listing, author=author)

@app.route('/post', methods=['GET', 'POST'])
@login_required
def post_listing():
    if request.method == 'POST':
        listing_data = {
            'user_id': current_user.id,
            'type': request.form.get('type'),
            'category': request.form.get('category'),
            'title': request.form.get('title'),
            'description': request.form.get('description'),
            'credits': int(request.form.get('credits', 1)),
            'ward': request.form.get('ward'),
            'status': 'open',
            'created_at': datetime.utcnow()
        }
        db.collection('listings').add(listing_data)
        return redirect(url_for('dashboard'))
        
    return render_template('post_listing.html')

@app.route('/profile/<id>')
def profile(id):
    profile_user_doc = db.collection('users').document(id).get()
    if not profile_user_doc.exists:
        return redirect(url_for('home'))
    profile_user = {'id': profile_user_doc.id, **profile_user_doc.to_dict()}
    
    listings_ref = db.collection('listings').where('user_id', '==', id).where('status', '==', 'open').stream()
    user_listings = [{'id': doc.id, **doc.to_dict()} for doc in listings_ref]
    
    return render_template('profile.html', profile_user=profile_user, listings=user_listings, reviews=[])

@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    user_ref = db.collection('users').document(current_user.id)
    if request.method == 'POST':
        name = request.form.get('name')
        ward = request.form.get('ward')
        bio = request.form.get('bio')
        avatar_style = request.form.get('avatar_style', 'initials')
        avatar_seed = request.form.get('avatar_seed') or name
        
        user_ref.update({
            'name': name,
            'ward': ward,
            'bio': bio,
            'avatar_style': avatar_style,
            'avatar_seed': avatar_seed
        })
        return redirect(url_for('profile', id=current_user.id))
        
    return render_template('edit_profile.html')

if __name__ == '__main__':
    socketio.run(app, debug=True)