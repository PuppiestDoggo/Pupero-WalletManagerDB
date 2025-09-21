import os
import json
from fastapi.testclient import TestClient
from app.main import app

class DummyChannel:
    def __init__(self, store):
        self.store = store
    def queue_declare(self, queue, durable=True):
        self.store['queue'] = queue
    def basic_publish(self, exchange, routing_key, body, properties=None):
        self.store['published'] = json.loads(body.decode('utf-8'))

class DummyConn:
    def __init__(self, store):
        self.store = store
    def channel(self):
        return DummyChannel(self.store)
    def close(self):
        pass


def test_withdraw_enqueues(monkeypatch):
    os.environ['RABBITMQ_URL'] = 'amqp://guest:guest@localhost:5672/%2F'

    import app.main as mainmod
    store = {}

    def fake_blocking_conn(params):
        return DummyConn(store)

    # Mock Monero addresses/balance calls to avoid HTTP
    def fake_fetch_real(user_id: int):
        return 0.0

    monkeypatch.setattr(mainmod, '_fetch_real_xmr', lambda user_id: 0.0)
    monkeypatch.setattr(mainmod.pika, 'BlockingConnection', fake_blocking_conn)

    client = TestClient(app)

    # Ensure balance exists
    r = client.get('/balance/1')
    assert r.status_code == 200

    # Request withdraw
    payload = {"to_address": "48...dest", "amount_xmr": 0.1}
    r2 = client.post('/withdraw/1', json=payload)
    assert r2.status_code == 200
    data = r2.json()
    assert data['tx_hash'] is None
    assert 'monero_result' in data

    # Verify message published
    assert store.get('queue')
    msg = store.get('published')
    assert msg and msg['type'] == 'withdraw'
    assert msg['to_address'] == payload['to_address']
    assert abs(msg['amount_xmr'] - payload['amount_xmr']) < 1e-12
