import os
import logging
import uuid
import random
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, session, jsonify, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from proxy import ProxyMiddleware
from flask_socketio import SocketIO, emit, join_room, leave_room
from dotenv import load_dotenv
from extensions import db
from models import ChatRoom, Message
from sqlalchemy import text

# Configure logging
logging.basicConfig(level=logging.DEBUG)

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key')
socketio = SocketIO(app, cors_allowed_origins="*")

# Configure SQLAlchemy
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///chat.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}

# Initialize SQLAlchemy with app
db.init_app(app)

# Proxy middleware
app.wsgi_app = ProxyMiddleware(app.wsgi_app)

# Store active users per room
active_users = {}
# Store soldiers with permission to speak
soldiers_with_permission = {}

def generate_room_id():
    return str(uuid.uuid4())[:8]

def cleanup_old_messages():
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    Message.query.filter(Message.timestamp < cutoff_time).delete()
    db.session.commit()

def init_db():
    with app.app_context():
        # Create tables
        db.create_all()
        
        # Check if owner_id column exists in chat_rooms table
        try:
            db.session.execute(text('SELECT owner_id FROM chat_rooms LIMIT 1'))
        except:
            # Add owner_id column if it doesn't exist
            db.session.execute(text('ALTER TABLE chat_rooms ADD COLUMN owner_id VARCHAR(36)'))
            db.session.commit()
            print("Added owner_id column to chat_rooms table")
        
        # Check if is_active column exists in chat_rooms table
        try:
            db.session.execute(text('SELECT is_active FROM chat_rooms LIMIT 1'))
        except:
            # Add is_active column if it doesn't exist
            db.session.execute(text('ALTER TABLE chat_rooms ADD COLUMN is_active BOOLEAN DEFAULT 1'))
            db.session.commit()
            print("Added is_active column to chat_rooms table")

        # Check if is_secret column exists in chat_rooms table
        try:
            db.session.execute(text('SELECT is_secret FROM chat_rooms LIMIT 1'))
        except:
            # Add is_secret column if it doesn't exist
            db.session.execute(text('ALTER TABLE chat_rooms ADD COLUMN is_secret BOOLEAN DEFAULT 0'))
            db.session.commit()
            print("Added is_secret column to chat_rooms table")

        # Check if member_limit column exists in chat_rooms table
        try:
            db.session.execute(text('SELECT member_limit FROM chat_rooms LIMIT 1'))
        except:
            # Add member_limit column if it doesn't exist
            db.session.execute(text('ALTER TABLE chat_rooms ADD COLUMN member_limit INTEGER DEFAULT 10'))
            db.session.commit()
            print("Added member_limit column to chat_rooms table")

# Initialize database with new schema
init_db()

rate_limits = {}

def rate_limit(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'No session'}), 403

        current_time = datetime.now()
        if user_id in rate_limits:
            last_request = rate_limits[user_id]
            if current_time - last_request < timedelta(seconds=1):
                return jsonify({'error': 'Rate limit exceeded'}), 429

        rate_limits[user_id] = current_time
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def before_request():
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())

@app.route('/')
def index():
    return render_template('chat.html')

@app.route('/create_room', methods=['POST'])
def create_room():
    username = request.form.get('username')
    custom_room_id = request.form.get('room_id')
    
    if not username:
        return redirect(url_for('index'))
    
    # Use custom room ID if provided, otherwise generate one
    room_id = custom_room_id if custom_room_id else generate_room_id()
    
    # Check if room ID already exists
    existing_room = ChatRoom.query.get(room_id)
    if existing_room:
        flash('Room ID already exists. Please choose another.')
        return redirect(url_for('index'))
    
    room = ChatRoom(id=room_id, owner_id=session['user_id'], is_active=True)
    db.session.add(room)
    db.session.commit()
    
    session['username'] = username
    return redirect(url_for('chat_room', room_id=room_id))

@app.route('/join_room', methods=['POST'])
def join_existing_room():
    username = request.form.get('username')
    room_id = request.form.get('room_id')
    
    if not username or not room_id:
        return redirect(url_for('index'))
    
    room = ChatRoom.query.get(room_id)
    if not room or not room.is_active:
        return redirect(url_for('index'))
    
    session['username'] = username
    return redirect(url_for('chat_room', room_id=room_id))

@app.route('/room/<room_id>')
def chat_room(room_id):
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))
    
    room = ChatRoom.query.get_or_404(room_id)
    if not room.is_active:
        return redirect(url_for('index'))
    
    is_owner = room.owner_id == session.get('user_id')
    role = 'Major' if is_owner else 'Soldier'
    return render_template('chat_room.html', room_id=room_id, username=username, is_owner=is_owner, role=role)

@app.route('/api/room/<room_id>/close', methods=['POST'])
def close_room(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    if room.owner_id != session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Delete all messages in the room
    Message.query.filter_by(room_id=room_id).delete()
    
    # Mark room as inactive
    room.is_active = False
    db.session.commit()
    
    # Notify all users in the room
    if room_id in active_users:
        emit('room_closed', {}, room=room_id, namespace='/')
        active_users.pop(room_id, None)
    
    return jsonify({'status': 'success'})

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('join')
def handle_join(data):
    username = data.get('username')
    room = data.get('room')
    
    if username and room:
        room_obj = ChatRoom.query.get(room)
        if not room_obj or not room_obj.is_active:
            emit('room_closed', {})
            return
        
        join_room(room)
        if room not in active_users:
            active_users[room] = {}
        active_users[room][username] = session.get('user_id')
        is_owner = room_obj.owner_id == session.get('user_id')
        role = 'Major' if is_owner else 'Soldier'
        emit('user_joined', {
            'username': username,
            'isOwner': is_owner,
            'role': role
        }, room=room)

@socketio.on('remove_user')
def handle_remove_user(data):
    room_id = data.get('room')
    username_to_remove = data.get('username')
    
    room = ChatRoom.query.get(room_id)
    if room and room.owner_id == session.get('user_id') and room.is_active:
        if room_id in active_users and username_to_remove in active_users[room_id]:
            user_id = active_users[room_id][username_to_remove]
            del active_users[room_id][username_to_remove]
            emit('user_removed', {'username': username_to_remove}, room=room_id)

@socketio.on('request_permission')
def handle_permission_request(data):
    username = data.get('username')
    room = data.get('room')
    
    room_obj = ChatRoom.query.get(room)
    if not room_obj or not room_obj.is_active:
        emit('room_closed', {})
        return
    
    # Emit permission request to the Major
    emit('permission_requested', {
        'username': username,
        'timestamp': datetime.now().strftime("%H:%M")
    }, room=room)

@socketio.on('grant_permission')
def handle_grant_permission(data):
    username = data.get('username')
    room = data.get('room')
    granted = data.get('granted', False)
    
    room_obj = ChatRoom.query.get(room)
    if room_obj and room_obj.owner_id == session.get('user_id'):
        if granted:
            if room not in soldiers_with_permission:
                soldiers_with_permission[room] = set()
            soldiers_with_permission[room].add(username)
        else:
            if room in soldiers_with_permission:
                soldiers_with_permission[room].discard(username)
        
        emit('permission_response', {
            'username': username,
            'granted': granted
        }, room=room)

@socketio.on('message')
def handle_message(data):
    username = session.get('username')
    message = data.get('message')
    room = data.get('room')
    
    room_obj = ChatRoom.query.get(room)
    if not room_obj or not room_obj.is_active:
        emit('room_closed', {})
        return
    
    # Allow messages from Major or soldiers with permission
    is_major = room_obj.owner_id == session.get('user_id')
    has_permission = room in soldiers_with_permission and username in soldiers_with_permission[room]
    
    if not is_major and not has_permission:
        emit('message_error', {'error': 'Only the Major or soldiers with permission can send messages'})
        return
    
    if username and message and room:
        timestamp = datetime.now().strftime("%H:%M")
        
        # Save message to database
        new_message = Message(
            id=str(uuid.uuid4()),
            message=message,
            user_id=username,
            room_id=room
        )
        db.session.add(new_message)
        db.session.commit()
        
        # Broadcast message to room
        emit('message', {
            'username': username,
            'message': message,
            'timestamp': timestamp,
            'isMajor': is_major
        }, room=room)

@socketio.on('disconnect')
def handle_disconnect():
    username = session.get('username')
    user_id = session.get('user_id')
    if username:
        for room_id in list(active_users.keys()):
            if username in active_users[room_id]:
                room = ChatRoom.query.get(room_id)
                if room and room.owner_id == user_id:
                    # Owner is leaving, close the room and remove all users
                    room.is_active = False
                    Message.query.filter_by(room_id=room_id).delete()
                    db.session.commit()
                    emit('room_closed', {}, room=room_id)
                    active_users.pop(room_id, None)
                else:
                    # Regular user leaving
                    del active_users[room_id][username]
                    emit('user_left', {'username': username}, room=room_id)

@app.route('/api/messages/<room_id>', methods=['GET'])
def get_messages(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    if not room.is_active:
        return jsonify({'error': 'Room is closed'}), 404
    
    messages = Message.query.filter_by(room_id=room_id).order_by(Message.timestamp.asc()).all()
    return jsonify({
        'messages': [{
            'id': msg.id,
            'message': msg.message,
            'timestamp': msg.timestamp.strftime("%H:%M"),
            'username': msg.user_id
        } for msg in messages]
    })

@app.route('/api/messages/<room_id>', methods=['POST'])
@rate_limit
def post_message(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    message = request.json.get('message', '').strip()
    if not message:
        return jsonify({'error': 'Empty message'}), 400

    new_message = Message(
        id=str(uuid.uuid4()),
        message=message,
        user_id=session['user_id'][:6],
        room_id=room_id
    )
    db.session.add(new_message)
    db.session.commit()
    return jsonify({'status': 'success'})

# Cleanup old messages periodically
@app.before_request
def cleanup():
    if random.random() < 0.01:  # 1% chance to cleanup on each request
        cleanup_old_messages()

# Add member limit functionality
@app.route('/api/room/<room_id>/set_limit', methods=['POST'])
def set_member_limit(room_id):
    room = ChatRoom.query.get_or_404(room_id)
    if room.owner_id != session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    limit = request.json.get('limit')
    if not isinstance(limit, int) or limit < 1:
        return jsonify({'error': 'Invalid limit'}), 400
    
    room.member_limit = limit
    db.session.commit()
    return jsonify({'status': 'success'})

@app.route('/create_secret_room', methods=['POST'])
def create_secret_room():
    username = request.form.get('username')
    if not username:
        return redirect(url_for('index'))
    
    room_id = generate_room_id()
    room = ChatRoom(
        id=room_id, 
        owner_id=session['user_id'], 
        is_active=True,
        is_secret=True,
        member_limit=request.form.get('member_limit', type=int) or 10
    )
    db.session.add(room)
    db.session.commit()
    
    session['username'] = username
    return redirect(url_for('secret_chat_room', room_id=room_id))

@app.route('/secret_room/<room_id>')
def secret_chat_room(room_id):
    username = session.get('username')
    if not username:
        return redirect(url_for('index'))
    
    room = ChatRoom.query.get_or_404(room_id)
    if not room.is_active or not room.is_secret:
        return redirect(url_for('index'))
    
    # Check member limit
    if len(active_users.get(room_id, {})) >= room.member_limit:
        flash('Room has reached maximum capacity')
        return redirect(url_for('index'))
    
    is_owner = room.owner_id == session.get('user_id')
    role = 'Major' if is_owner else 'Soldier'
    return render_template('secret_chat_room.html', room_id=room_id, username=username, is_owner=is_owner, role=role)

if __name__ == '__main__':
    socketio.run(app, debug=True)