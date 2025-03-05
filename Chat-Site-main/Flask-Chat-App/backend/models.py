from datetime import datetime
from extensions import db

class ChatRoom(db.Model):
    __tablename__ = 'chat_rooms'
    
    id = db.Column(db.String(8), primary_key=True)
    owner_id = db.Column(db.String(36), nullable=False)  # Store the owner's user_id
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)  # Track if room is active or closed
    is_secret = db.Column(db.Boolean, default=False)  # Track if room is a secret Mars room
    member_limit = db.Column(db.Integer, default=10)  # Maximum number of members allowed
    messages = db.relationship('Message', backref='room', lazy=True)

class Message(db.Model):
    __tablename__ = 'messages'
    
    id = db.Column(db.String(36), primary_key=True)
    message = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.String(36), nullable=False)
    room_id = db.Column(db.String(8), db.ForeignKey('chat_rooms.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Message {self.id}>' 