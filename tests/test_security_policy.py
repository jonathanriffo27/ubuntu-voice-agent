"""Tests de la política de seguridad por tiers de riesgo (Fase 0)."""
import os

from src.security.policy import SecurityPolicy, RiskTier


class TestDefaults:
    def test_defaults_si_archivo_no_existe(self, tmp_path):
        policy = SecurityPolicy.load(path=str(tmp_path / "inexistente.yaml"))
        assert policy.default_tier == RiskTier.LOCAL_WRITE

    def test_lectura_es_tier1(self):
        policy = SecurityPolicy()
        assert policy.classify("analizar_pantalla") == RiskTier.READ_ONLY
        assert policy.classify("leer_archivo") == RiskTier.READ_ONLY
        assert policy.classify("buscar_web") == RiskTier.READ_ONLY

    def test_envio_es_tier3(self):
        policy = SecurityPolicy()
        assert policy.classify("enviar_whatsapp") == RiskTier.IRREVERSIBLE
        assert policy.classify("enviar_correo") == RiskTier.IRREVERSIBLE
        assert policy.classify("ejecutar_comando_desarrollo") == RiskTier.IRREVERSIBLE

    def test_desconocido_cae_en_default(self):
        policy = SecurityPolicy()  # default_tier=local_write
        assert policy.classify("herramienta_nueva_xyz") == RiskTier.LOCAL_WRITE

    def test_glob_patterns(self):
        policy = SecurityPolicy()
        assert policy.classify("capturar_pantalla_desarrollo") == RiskTier.READ_ONLY
        assert policy.classify("abrir_whatsapp") == RiskTier.LOCAL_WRITE


class TestEscalation:
    """La escalación por contenido siempre gana sobre el nombre."""

    def test_payload_pago_eleva_a_tier3(self):
        policy = SecurityPolicy()
        assert policy.classify("interactuar_gui", "haz click en Confirmar compra") == RiskTier.IRREVERSIBLE

    def test_payload_tarjeta_eleva_a_tier3(self):
        policy = SecurityPolicy()
        assert policy.classify("interactuar_gui", "escribe 4111111111111111 en el campo") == RiskTier.IRREVERSIBLE

    def test_payload_rm_rf_eleva_a_tier3(self):
        policy = SecurityPolicy()
        assert policy.classify("proponer_comando", "rm -rf /") == RiskTier.IRREVERSIBLE

    def test_payload_inocuo_no_escala(self):
        policy = SecurityPolicy()
        assert policy.classify("interactuar_gui", "haz click en Enviar mensaje") == RiskTier.LOCAL_WRITE

    def test_ruta_clave_privada_escala_a_tier3(self):
        """Incidente real: 'cat ~/.ssh/id_rsa' no debe tratarse como lectura inocua."""
        policy = SecurityPolicy()
        assert policy.classify("leer_archivo", "/home/user/.ssh/id_rsa") == RiskTier.IRREVERSIBLE
        assert policy.classify("leer_archivo", "~/.ssh/id_ed25519") == RiskTier.IRREVERSIBLE
        assert policy.classify("proponer_comando", "cat ~/.ssh/id_rsa") == RiskTier.IRREVERSIBLE

    def test_archivos_de_secretos_escalan_a_tier3(self):
        policy = SecurityPolicy()
        assert policy.classify("leer_archivo", ".env") == RiskTier.IRREVERSIBLE
        assert policy.classify("leer_archivo", "config/.env.production") == RiskTier.IRREVERSIBLE
        assert policy.classify("leer_archivo", "~/.aws/credentials") == RiskTier.IRREVERSIBLE
        assert policy.classify("leer_archivo", "server.pem") == RiskTier.IRREVERSIBLE

    def test_rutas_inocuas_no_escalan(self):
        policy = SecurityPolicy()
        assert policy.classify("leer_archivo", "README.md") == RiskTier.READ_ONLY
        assert policy.classify("leer_archivo", "src/plugins/shell/tools.py") == RiskTier.READ_ONLY
        assert policy.classify("leer_archivo", "environment.yml") == RiskTier.READ_ONLY

    def test_requires_hitl(self):
        policy = SecurityPolicy()
        assert policy.requires_hitl("enviar_whatsapp") is True
        assert policy.requires_hitl("analizar_pantalla") is False
        assert policy.requires_hitl("leer_archivo", "~/.ssh/id_rsa") is True


class TestYamlReal:
    """El YAML del repo se carga sin errores y es coherente."""

    def test_carga_yaml_real(self):
        policy = SecurityPolicy.load()  # config/security_policy.yaml del repo
        assert policy.default_tier == RiskTier.LOCAL_WRITE
        assert policy.classify("analizar_pantalla") == RiskTier.READ_ONLY
        assert policy.classify("enviar_telegram") == RiskTier.IRREVERSIBLE
        assert policy.classify("interactuar_gui") == RiskTier.LOCAL_WRITE

    def test_yaml_real_escala_rutas_sensibles(self):
        """El YAML del repo (que pisa los defaults) incluye las rutas sensibles."""
        policy = SecurityPolicy.load()
        assert policy.classify("leer_archivo", "~/.ssh/id_rsa") == RiskTier.IRREVERSIBLE
        assert policy.classify("leer_archivo", ".env") == RiskTier.IRREVERSIBLE
        assert policy.classify("leer_archivo", "README.md") == RiskTier.READ_ONLY
