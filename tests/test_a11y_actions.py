import pytest
from unittest.mock import MagicMock
from src.utils.a11y import AccessibilitySensor


def test_set_element_text_with_mock():
    sensor = AccessibilitySensor()
    mock_node = MagicMock()
    mock_ed = MagicMock()
    mock_ed.set_text_contents.return_value = True
    mock_node.get_editable_text_iface.return_value = mock_ed

    # Forzar _atspi_gobject_ready para la prueba unitaria
    sensor._atspi_gobject_ready = True
    success = sensor.set_element_text(mock_node, "Hola desde Atlas CUA")
    assert success is True
    mock_ed.set_text_contents.assert_called_once_with("Hola desde Atlas CUA")


def test_click_element_action_with_mock():
    sensor = AccessibilitySensor()
    mock_node = MagicMock()
    mock_act = MagicMock()
    mock_act.get_n_actions.return_value = 1
    mock_act.do_action.return_value = True
    mock_act.get_action_name.return_value = "click"
    mock_node.get_action_iface.return_value = mock_act

    sensor._atspi_gobject_ready = True
    success = sensor.click_element_action(mock_node, action_index=0)
    assert success is True
    mock_act.do_action.assert_called_once_with(0)


def test_get_element_bounds_with_mock():
    sensor = AccessibilitySensor()
    mock_node = MagicMock()
    mock_comp = MagicMock()
    
    mock_rect = MagicMock()
    mock_rect.x = 100
    mock_rect.y = 200
    mock_rect.width = 300
    mock_rect.height = 400
    mock_comp.get_extents.return_value = mock_rect
    mock_node.get_component_iface.return_value = mock_comp

    sensor._atspi_gobject_ready = True
    bounds = sensor.get_element_bounds(mock_node)
    assert bounds == (100, 200, 300, 400)


def test_find_interactive_nodes_with_mock():
    sensor = AccessibilitySensor()
    root = MagicMock()
    root.get_role_name.return_value = "window"
    root.get_name.return_value = "App Window"
    root.get_state_set.return_value = None
    root.get_action_iface.return_value = None
    
    btn = MagicMock()
    btn.get_role_name.return_value = "push_button"
    btn.get_name.return_value = "Enviar"
    btn.get_state_set.return_value = None
    btn.get_child_count.return_value = 0
    mock_comp = MagicMock()
    mock_rect = MagicMock(x=50, y=50, width=80, height=30)
    mock_comp.get_extents.return_value = mock_rect
    btn.get_component_iface.return_value = mock_comp

    act = MagicMock()
    act.get_n_actions.return_value = 1
    act.get_action_name.return_value = "press"
    btn.get_action_iface.return_value = act

    root.get_child_count.return_value = 1
    root.get_child_at_index.return_value = btn

    sensor._atspi_gobject_ready = True
    interactive = sensor.find_interactive_nodes(root)
    assert len(interactive) == 1
    assert interactive[0]["name"] == "Enviar"
    assert interactive[0]["role"] == "push_button"
    assert "press" in interactive[0]["actions"]
