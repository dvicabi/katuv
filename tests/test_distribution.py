import hashlib
import io
import json
from pathlib import Path
import time
import urllib.request
import zipfile
import threading
import errno

from fastapi.testclient import TestClient
import pytest

from autocaption.bootstrap import download, safe_extract, Preparation
from autocaption.web import create_app


def test_desktop_only_has_no_pairing_or_network_access(tmp_path):
    app = create_app(tmp_path/'מחשב חדש')
    with TestClient(app) as client:
        assert client.get('/api/health').json()['device'] == 'computer'
        assert not hasattr(app.state, 'phone')
        assert 'phoneButton' not in client.get('/').text
        assert client.get('/api/phone').status_code == 404
        assert client.post('/api/phone/start').status_code == 404
        assert client.get('/pair').status_code == 404
        assert client.get('/api/projects', headers={'Host':'192.168.1.2'}).status_code == 403
        assert client.post('/api/demo', headers={'Origin':'http://attacker.example'}).status_code == 403
    assert app.state.studio.executor._shutdown


def test_download_is_atomic_and_checks_hash(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        headers = {'Content-Length':'7'}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: Response(b'payload'))
    target = tmp_path/'tool.zip'
    target.write_bytes(b'previous')
    with pytest.raises(ValueError):
        download('https://publisher/tool.zip', target, '0'*64)
    assert target.read_bytes() == b'previous'
    assert not target.with_suffix('.part').exists()
    download('https://publisher/tool.zip', target, hashlib.sha256(b'payload').hexdigest())
    assert target.read_bytes() == b'payload'


@pytest.mark.parametrize('name', ['../escape.exe', '/absolute.exe', 'C:/escape.exe', '..\\escape.exe'])
def test_archive_cannot_escape_destination(tmp_path, name):
    archive = tmp_path/'input.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('safe.txt', 'safe')
        bundle.writestr(name, 'bad')
    with pytest.raises(ValueError):
        safe_extract(archive, tmp_path/'output')
    assert not (tmp_path/'output/safe.txt').exists()


def test_preparation_reports_failure_and_never_downloads_optional_weights_by_default(tmp_path, monkeypatch):
    manager = Preparation()
    calls = []
    monkeypatch.setattr('autocaption.bootstrap.ensure_ffmpeg', lambda update: calls.append('ffmpeg'))
    def prepare(update, optional):
        calls.append(optional)
        raise RuntimeError('network unavailable')
    monkeypatch.setattr('autocaption.bootstrap.prepare_models', prepare)
    with TestClient(create_app(tmp_path)) as client:
        manager.start(client.app.state.studio)
        client.app.state.studio.executor.submit(lambda: None).result()
    assert calls == ['ffmpeg', False]
    assert manager.snapshot()['state'] == 'error'
    assert 'נסו שוב' in manager.snapshot()['error']


def test_per_user_data_override_wins_over_legacy(tmp_path, monkeypatch):
    from autocaption.paths import user_home, project_dir
    target = tmp_path/'משתמש חדש'
    monkeypatch.setenv('AUTOCAPTION_HOME', str(target))
    monkeypatch.setenv('AUTOCAPTION_DATA_DIR', str(target/'projects'))
    assert user_home() == target.resolve()
    assert project_dir() == (target/'projects').resolve()


@pytest.mark.parametrize('content', ['{invalid', '[]', '{"id":"wrong"}'])
def test_damaged_project_never_prevents_startup_or_listing(tmp_path, content):
    folder = tmp_path / ('a' * 32)
    folder.mkdir()
    damaged = folder / 'project.json'
    damaged.write_text(content, encoding='utf-8')
    with TestClient(create_app(tmp_path)) as client:
        rows = client.get('/api/projects').json()
        assert rows[0]['state'] == 'damaged'
        assert client.get('/api/projects/' + 'a' * 32).status_code == 409
        assert client.get('/api/health').status_code == 200
    assert damaged.read_text(encoding='utf-8') == content


def test_preparation_is_busy_and_prevents_maintenance_or_preferences(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr('autocaption.bootstrap.ensure_ffmpeg', lambda update: None)
    def preparing(update, optional):
        entered.set()
        assert release.wait(10)
    monkeypatch.setattr('autocaption.bootstrap.prepare_models', preparing)
    monkeypatch.setenv('AUTOCAPTION_INSTALLATION_ID', 'test-installation')
    with TestClient(create_app(tmp_path)) as client:
        try:
            assert client.post('/api/system/prepare', json={}).status_code == 200
            assert entered.wait(5)
            health = client.get('/api/health').json()
            assert health['version'] == 2 and health['busy']
            assert health['installation_id'] == 'test-installation'
            assert health['app_version'] and health['runtime_id']
            assert client.post('/api/system/maintenance', json={'enabled': True}).status_code == 409
            assert client.put('/api/system/preferences', json={'include_noncommercial': True}).status_code == 409
        finally:
            release.set()
            client.app.state.studio.executor.submit(lambda: None).result(timeout=5)
        assert not client.get('/api/health').json()['busy']
        assert client.post('/api/system/maintenance', json={'enabled': True}).status_code == 200
        assert client.post('/api/demo', json={}).status_code == 409
        assert client.post('/api/system/prepare', json={}).status_code == 409
        assert client.post('/api/system/maintenance', json={'enabled': False}).status_code == 200


def test_noncommercial_choice_survives_restart_and_can_be_revoked(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert not client.app.state.studio.preferences()['include_noncommercial']
        assert client.put('/api/system/preferences', json={'include_noncommercial': True}).status_code == 200
    with TestClient(create_app(tmp_path)) as client:
        assert client.app.state.studio.preferences()['include_noncommercial']
        assert client.put('/api/system/preferences', json={'include_noncommercial': False}).status_code == 200
    with TestClient(create_app(tmp_path)) as client:
        assert not client.app.state.studio.preferences()['include_noncommercial']


def test_failed_uploads_leave_no_project_directory(tmp_path, monkeypatch):
    from autocaption.web import Studio
    from fastapi import HTTPException
    studio = Studio(tmp_path)
    try:
        monkeypatch.setattr('autocaption.media.probe_video_size', lambda _: None)
        monkeypatch.setattr('autocaption.media.probe_duration', lambda _: 0)
        with pytest.raises(HTTPException):
            studio.create(io.BytesIO(b'not a video'), 'broken.mp4')
        assert not list(tmp_path.iterdir())
        class Disconnected(io.BytesIO):
            def read(self, size):
                raise ConnectionError('upload interrupted')
        with pytest.raises(ConnectionError):
            studio.create(Disconnected(), 'interrupted.mp4')
        assert not list(tmp_path.iterdir())
    finally:
        studio.executor.shutdown()


def test_disk_full_has_actionable_error_and_upload_is_removed(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('autocaption.web.shutil.disk_usage', lambda _: SimpleNamespace(free=1))
    with TestClient(create_app(tmp_path)) as client:
        response = client.post('/api/projects', files={'video': ('clip.mp4', b'abc', 'video/mp4')})
        assert response.status_code == 507
        assert 'מקום פנוי' in response.json()['detail']
    assert not list(tmp_path.iterdir())


def test_preparation_network_and_disk_errors_are_actionable():
    from autocaption.bootstrap import preparation_error
    assert 'אינטרנט' in preparation_error(urllib.error.URLError('offline'))
    assert 'מקום פנוי' in preparation_error(OSError(errno.ENOSPC, 'disk full'))
