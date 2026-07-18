import pytest
import json
from server.app import app, users_db, todos_db

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        # Clear databases before each test
        users_db.clear()
        todos_db.clear()
        yield client

def test_register_new_user(client):
    response = client.post('/auth/register',
                          json={'username': 'testuser', 'password': 'testpass'},
                          content_type='application/json')
    assert response.status_code == 201
    data = json.loads(response.data)
    assert 'user_id' in data
    assert data['username'] == 'testuser'

def test_register_duplicate_user(client):
    # Register first user
    client.post('/auth/register',
               json={'username': 'testuser', 'password': 'testpass'},
               content_type='application/json')
    
    # Try to register same user
    response = client.post('/auth/register',
                         json={'username': 'testuser', 'password': 'testpass'},
                         content_type='application/json')
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error'] == 'Username already taken'

def test_login_valid_user(client):
    # Register user first
    client.post('/auth/register',
               json={'username': 'testuser', 'password': 'testpass'},
               content_type='application/json')
    
    # Login
    response = client.post('/auth/login',
                          json={'username': 'testuser', 'password': 'testpass'},
                          content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'token' in data
    assert data['expires_in'] == 3600

def test_login_invalid_user(client):
    response = client.post('/auth/login',
                          json={'username': 'wronguser', 'password': 'wrongpass'},
                          content_type='application/json')
    assert response.status_code == 401
    data = json.loads(response.data)
    assert data['error'] == 'Invalid username or password'

def test_access_todos_without_token(client):
    response = client.get('/todos')
    assert response.status_code == 401
    data = json.loads(response.data)
    assert data['error'] == 'Token is missing'

def test_todos_crud_with_valid_token(client):
    # Register and login
    client.post('/auth/register',
               json={'username': 'testuser', 'password': 'testpass'},
               content_type='application/json')
    
    login_response = client.post('/auth/login',
                                json={'username': 'testuser', 'password': 'testpass'},
                                content_type='application/json')
    token = json.loads(login_response.data)['token']
    
    # Test GET empty todos
    response = client.get('/todos',
                         headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['todos'] == []
    
    # Test POST todo
    response = client.post('/todos',
                          json={'title': 'Test todo'},
                          headers={'Authorization': f'Bearer {token}',
                                  'Content-Type': 'application/json'})
    assert response.status_code == 201
    todo_data = json.loads(response.data)
    todo_id = todo_data['id']
    assert todo_data['title'] == 'Test todo'
    assert todo_data['completed'] == False
    
    # Test GET todos with one item
    response = client.get('/todos',
                         headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data['todos']) == 1
    assert data['todos'][0]['title'] == 'Test todo'
    
    # Test PUT todo
    response = client.put(f'/todos/{todo_id}',
                         json={'completed': True},
                         headers={'Authorization': f'Bearer {token}',
                                  'Content-Type': 'application/json'})
    assert response.status_code == 200
    updated_data = json.loads(response.data)
    assert updated_data['completed'] == True
    
    # Test DELETE todo
    response = client.delete(f'/todos/{todo_id}',
                           headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 204
    
    # Verify deletion
    response = client.get('/todos',
                         headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200
    data = json.loads(response.data)
    assert len(data['todos']) == 0