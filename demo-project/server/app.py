from flask import Flask, request, jsonify
import jwt
import datetime
import uuid
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-for-jwt'

# In-memory databases for demo purposes
users_db = {}
todos_db = {}

# Helper to generate JWT token
def generate_token(user_id, username):
    payload = {
        'user_id': str(user_id),
        'username': username,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

# JWT authentication decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'error': 'Token is missing'}), 401
        
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user_id = data['user_id']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        
        return f(current_user_id, *args, **kwargs)
    
    return decorated

# Auth endpoints
@app.route('/auth/register', methods=['POST'])
def register():
    data = request.get_json()
    
    if not data or 'username' not in data or 'password' not in data:
        return jsonify({'error': 'Username and password required'}), 400
    
    username = data['username']
    password = data['password']
    
    # Check if user already exists
    for user_id, user in users_db.items():
        if user['username'] == username:
            return jsonify({'error': 'Username already taken'}), 400
    
    # Create new user
    user_id = str(uuid.uuid4())
    users_db[user_id] = {
        'id': user_id,
        'username': username,
        'password': password  # In real app, store hash instead
    }
    
    return jsonify({'user_id': user_id, 'username': username}), 201

@app.route('/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    
    if not data or 'username' not in data or 'password' not in data:
        return jsonify({'error': 'Username and password required'}), 400
    
    username = data['username']
    password = data['password']
    
    # Find user
    user = None
    for user_id, user_data in users_db.items():
        if user_data['username'] == username and user_data['password'] == password:
            user = user_data
            user['id'] = user_id
            break
    
    if not user:
        return jsonify({'error': 'Invalid username or password'}), 401
    
    # Generate token
    token = generate_token(user['id'], user['username'])
    
    return jsonify({'token': token, 'expires_in': 3600}), 200

# Todo endpoints
@app.route('/todos', methods=['GET'])
@token_required
def get_todos(current_user_id):
    user_todos = []
    for todo_id, todo in todos_db.items():
        if todo['user_id'] == current_user_id:
            user_todos.append({
                'id': todo_id,
                'title': todo['title'],
                'completed': todo['completed']
            })
    
    return jsonify({'todos': user_todos}), 200

@app.route('/todos', methods=['POST'])
@token_required
def create_todo(current_user_id):
    data = request.get_json()
    
    if not data or 'title' not in data:
        return jsonify({'error': 'Title is required'}), 400
    
    title = data['title']
    
    # Create new todo
    todo_id = str(uuid.uuid4())
    todos_db[todo_id] = {
        'id': todo_id,
        'title': title,
        'completed': False,
        'user_id': current_user_id
    }
    
    return jsonify({
        'id': todo_id,
        'title': title,
        'completed': False
    }), 201

@app.route('/todos/<todo_id>', methods=['PUT'])
@token_required
def update_todo(current_user_id, todo_id):
    if todo_id not in todos_db:
        return jsonify({'error': 'Todo not found'}), 404
    
    todo = todos_db[todo_id]
    
    # Check ownership
    if todo['user_id'] != current_user_id:
        return jsonify({'error': 'Todo not found'}), 404
    
    data = request.get_json()
    
    if 'title' in data:
        todo['title'] = data['title']
    if 'completed' in data:
        todo['completed'] = data['completed']
    
    return jsonify({
        'id': todo_id,
        'title': todo['title'],
        'completed': todo['completed']
    }), 200

@app.route('/todos/<todo_id>', methods=['DELETE'])
@token_required
def delete_todo(current_user_id, todo_id):
    if todo_id not in todos_db:
        return jsonify({'error': 'Todo not found'}), 404
    
    todo = todos_db[todo_id]
    
    # Check ownership
    if todo['user_id'] != current_user_id:
        return jsonify({'error': 'Todo not found'}), 404
    
    del todos_db[todo_id]
    
    return '', 204

if __name__ == '__main__':
    app.run(debug=True, port=5000)