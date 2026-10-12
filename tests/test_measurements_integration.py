from fastapi.testclient import TestClient
from studio.app import create_app


def test_metrics_routes_are_installed_in_real_app_without_test_helper(tmp_path):
    with TestClient(create_app(tmp_path),base_url='http://127.0.0.1:8787') as c:
        response=c.get('/api/measurements')
        assert response.status_code==200
        assert response.json()=={'items':[]}
        assert c.get('/api/measurements/export').status_code==200
        assert c.post('/api/measurements',json={}).status_code==403
