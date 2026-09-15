import pytest
from unittest.mock import patch, MagicMock
from src.utils.a11y import AccessibilitySensor


def test_a11y_sensor_init():
    sensor = AccessibilitySensor()
    assert "/at-spi/bus" in sensor.bus_path
    assert "unix:path=" in sensor.bus_address


def test_a11y_sensor_mock_list_apps():
    sensor = AccessibilitySensor()
    sensor._atspi_gobject_ready = False
    
    mock_stdout = (
        "NAME                      PID PROCESS         USER     CONNECTION\n"
        ":1.0                     4496 gnome-shell     jonathan :1.0\n"
        ":1.77                   74876 brave           jonathan :1.77\n"
        ":1.85                   12345 Telegram        jonathan :1.85\n"
    )
    
    with patch("os.path.exists", return_value=True), \
         patch("subprocess.run") as mock_run:
        
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = mock_stdout
        mock_run.return_value = mock_proc
        
        apps = sensor.list_accessible_apps()
        assert len(apps) == 3
        assert apps[1]["process"] == "brave"
        assert apps[1]["name"] == ":1.77"
        
        conn_brave = sensor.find_app_connection("brave")
        assert conn_brave == ":1.77"
        
        conn_tg = sensor.find_app_connection("telegram")
        assert conn_tg == ":1.85"


def test_a11y_sensor_get_child_count():
    sensor = AccessibilitySensor()
    
    with patch("os.path.exists", return_value=True), \
         patch("subprocess.run") as mock_run:
        
        # Simular respuesta de GetChildren de busctl
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = 'a(so) 2 ":1.77" "/org/a11y/atspi/accessible/1" ":1.77" "/org/a11y/atspi/accessible/2"'
        mock_run.return_value = mock_proc
        
        count = sensor.get_child_count(":1.77")
        assert count == 2


def test_a11y_sensor_wait_for_app_ready_success():
    sensor = AccessibilitySensor()
    sensor._atspi_gobject_ready = False
    
    with patch.object(sensor, "is_available", return_value=True), \
         patch.object(sensor, "find_app_connection", return_value=":1.77"), \
         patch.object(sensor, "get_child_count", return_value=1):
        
        ready = sensor.wait_for_app_ready("brave", timeout=1.0)
        assert ready is True


def test_whatsapp_state_detection_loading():
    sensor = AccessibilitySensor()
    
    mock_win = MagicMock()
    mock_win.get_role_name.return_value = "frame"
    mock_win.get_name.return_value = "web.whatsapp.com"
    mock_win.get_description.return_value = ""
    mock_win.get_state_set.return_value = None
    
    mock_loading_child = MagicMock()
    mock_loading_child.get_role_name.return_value = "static"
    mock_loading_child.get_name.return_value = "Cargando tus chats..."
    mock_loading_child.get_description.return_value = ""
    mock_loading_child.get_state_set.return_value = None
    mock_loading_child.get_child_count.return_value = 0
    
    mock_win.get_child_count.return_value = 1
    mock_win.get_child_at_index.return_value = mock_loading_child
    
    state, detail = sensor.get_whatsapp_state(mock_win)
    assert state == "LOADING"
    assert "Cargando" in detail


def test_whatsapp_state_detection_qr():
    sensor = AccessibilitySensor()
    
    mock_win = MagicMock()
    mock_win.get_role_name.return_value = "frame"
    mock_win.get_name.return_value = "web.whatsapp.com"
    mock_win.get_description.return_value = ""
    mock_win.get_state_set.return_value = None
    
    mock_qr_child = MagicMock()
    mock_qr_child.get_role_name.return_value = "heading"
    mock_qr_child.get_name.return_value = "Para usar WhatsApp en tu computadora"
    mock_qr_child.get_description.return_value = "Escanear código QR"
    mock_qr_child.get_state_set.return_value = None
    mock_qr_child.get_child_count.return_value = 0
    
    mock_win.get_child_count.return_value = 1
    mock_win.get_child_at_index.return_value = mock_qr_child
    
    state, detail = sensor.get_whatsapp_state(mock_win)
    assert state == "QR_REQUIRED"
    assert "QR" in detail


def test_whatsapp_state_detection_ready():
    sensor = AccessibilitySensor()
    
    mock_win = MagicMock()
    mock_win.get_role_name.return_value = "frame"
    mock_win.get_name.return_value = "web.whatsapp.com"
    mock_win.get_description.return_value = ""
    mock_win.get_state_set.return_value = None
    
    mock_search = MagicMock()
    mock_search.get_role_name.return_value = "entry"
    mock_search.get_name.return_value = "Buscar un chat o iniciar uno nuevo"
    mock_search.get_description.return_value = ""
    mock_search.get_state_set.return_value = None
    mock_search.get_child_count.return_value = 0
    
    mock_win.get_child_count.return_value = 1
    mock_win.get_child_at_index.return_value = mock_search
    
    state, detail = sensor.get_whatsapp_state(mock_win)
    assert state == "READY"
    assert "search_entry" in detail


def test_wait_for_whatsapp_ready_qr_fail():
    sensor = AccessibilitySensor()
    with patch.object(sensor, "is_available", return_value=True), \
         patch.object(sensor, "get_whatsapp_window", return_value=MagicMock()), \
         patch.object(sensor, "get_whatsapp_state", return_value=("QR_REQUIRED", "Requiere QR")):
        
        ready, msg = sensor.wait_for_whatsapp_ready(timeout=1.0)
        assert ready is False
        assert "QR" in msg
