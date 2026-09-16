"""Tests del sandbox bwrap (Fase 6): construcción de argv, red, y ejecución real."""
import os
import shutil
import subprocess

import pytest

from src.security.sandbox import BubblewrapSandbox, command_needs_network


PROJECT = "/home/jonathan/proyectos/voice_agent"


def _shutil_rmtree(path):
    shutil.rmtree(path, ignore_errors=True)


class TestArgvConstruction:
    def test_estructura_base(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: {})
        argv = sb.build_argv("ls -la")
        assert argv[0].endswith("bwrap")
        joined = " ".join(argv)
        assert "--unshare-net" in joined          # red OFF por defecto
        assert "--bind" in argv and str(tmp_path) in argv  # proyecto escribible
        assert "--die-with-parent" in joined
        assert "--new-session" in joined
        assert "--clearenv" in joined              # entorno vacío por defecto
        # Nunca expone las credenciales del usuario:
        assert ".ssh" not in joined and ".aws" not in joined and ".gnupg" not in joined
        # El comando viaja como argumento único de sh -c (sin shell intermedio)
        assert argv[-3:] == ["sh", "-c", "ls -la"]

    def test_red_on_elimina_unshare_net(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: {})
        argv = sb.build_argv("pip install x", network=True)
        assert "--unshare-net" not in argv

    def test_home_es_tmp_aislado(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: {})
        argv = sb.build_argv("ls")
        pairs = [(argv[j + 1], argv[j + 2]) for j, a in enumerate(argv)
                 if a == "--setenv" and j + 2 < len(argv)]
        assert ("HOME", "/tmp") in pairs

    def test_extra_ro_binds_antes_del_bind_rw(self, tmp_path):
        """Modo worktree (Fase 3): repo principal ro-bind ANTES, worktree rw después."""
        repo = tmp_path / "repo"
        worktree = repo / ".worktrees" / "agent-x"
        worktree.mkdir(parents=True)
        sb = BubblewrapSandbox(str(worktree), env_builder=lambda: {},
                               extra_ro_binds=[str(repo)])
        argv = sb.build_argv("ls")
        # Encontrar los índices: ro-bind repo < bind worktree
        ro_idx = [i for i, v in enumerate(argv)
                  if v == "--ro-bind" and argv[i + 1] == str(repo)]
        rw_idx = [i for i, v in enumerate(argv)
                  if v == "--bind" and argv[i + 1] == str(worktree)]
        assert ro_idx and rw_idx, "faltan los mounts"
        assert max(ro_idx) < min(rw_idx), "el repo ro debe montarse antes que el worktree rw"

    def test_env_limpio_se_inyecta_como_setenv(self, tmp_path):
        env = {"PATH": "/usr/bin", "LANG": "C.UTF-8", "TAVILY_API_KEY": "secreto"}
        # Filtro previo: el env que llega a build_argv YA viene limpio del broker;
        # aquí comprobamos que SOLO lo que se pasa queda dentro.
        clean = {k: v for k, v in env.items() if "KEY" not in k}
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: dict(env))
        argv = sb.build_argv("ls", env=clean)
        joined = " ".join(argv)
        assert "TAVILY_API_KEY" not in joined and "secreto" not in joined
        pairs = [(argv[j + 1], argv[j + 2]) for j, a in enumerate(argv)
                 if a == "--setenv" and j + 2 < len(argv)]
        assert ("PATH", "/usr/bin") in pairs and ("LANG", "C.UTF-8") in pairs


class TestNetworkHeuristic:
    @pytest.mark.parametrize("cmd", [
        "pip install requests",
        "pip3 download numpy",
        "git clone https://github.com/x/y",
        "git pull",
        "git push origin main",
        "curl https://api.ejemplo.com",
        "wget https://x",
        "apt update",
        "/home/x/venv/bin/pip install flask",
        "npm install",
    ])
    def test_necesitan_red(self, cmd):
        assert command_needs_network(cmd) is True

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "git status",
        "git log --oneline",
        "pip list",
        "python --version",
        "./venv/bin/pytest -q",
        "cat archivo.txt",
        "grep -r foo src/",
    ])
    def test_no_necesitan_red(self, cmd):
        assert command_needs_network(cmd) is False


class TestEnvScrubbing:
    def test_env_builder_se_aplica(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path),
                               env_builder=lambda: {"PATH": "/usr/bin", "FOO": "1"})
        # Sin ejecutar: solo comprobamos que run usaría ese env (inyección)
        assert sb._env_builder() == {"PATH": "/usr/bin", "FOO": "1"}


@pytest.mark.skipif(not shutil.which("bwrap"), reason="bwrap no instalado")
class TestEjecucionReal:
    def test_echo_funciona_en_sandbox(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: {"PATH": "/usr/bin:/bin"})
        res = sb.run("echo atlas-sandbox-ok", network=False)
        assert res.returncode == 0
        assert "atlas-sandbox-ok" in res.stdout

    def test_sin_red_falla_conexion(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: {"PATH": "/usr/bin:/bin"})
        # Con --unshare-net, incluso resolver/conectar debe fallar
        res = sb.run("curl -sS -m 3 https://example.com", network=False, timeout=15)
        assert res.returncode != 0

    def test_home_es_tmp_y_sin_ssh(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: {"PATH": "/usr/bin:/bin"})
        res = sb.run("echo $HOME; test -d $HOME/.ssh && echo HAY_SSH || echo SIN_SSH")
        assert "$HOME" not in res.stdout
        assert "/tmp" in res.stdout
        assert "SIN_SSH" in res.stdout

    def test_proyecto_es_escribible(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: {"PATH": "/usr/bin:/bin"})
        res = sb.run("touch archivo_sandbox.txt && echo escrito")
        assert res.returncode == 0, res.stderr
        assert (tmp_path / "archivo_sandbox.txt").exists()

    def test_fuera_del_proyecto_es_solo_lectura(self, tmp_path):
        sb = BubblewrapSandbox(str(tmp_path), env_builder=lambda: {"PATH": "/usr/bin:/bin"})
        res = sb.run(f"sh -c 'echo x > /usr/atlas_no_debe.txt'")
        assert res.returncode != 0
        assert not os.path.exists("/usr/atlas_no_debe.txt")

    def test_secretos_no_llegan_al_hijo(self, tmp_path):
        """Cadena completa broker→sandbox: el entorno del padre contiene la key,
        el hijo sandboxed nunca la ve (ni aunque pida `env`)."""
        from src.security.credentials import CredentialBroker
        parent_env = {"PATH": "/usr/bin:/bin", "TAVILY_API_KEY": "tvly-secreto-largo-123"}
        broker = CredentialBroker(parent_env)
        sb = BubblewrapSandbox(str(tmp_path), env_builder=broker.scrub_env)
        res = sb.run("env")
        assert res.returncode == 0
        assert "tvly-secreto-largo-123" not in res.stdout
        assert "TAVILY_API_KEY" not in res.stdout

    def test_venv_accesible_desde_layout_worktree(self, tmp_path):
        """Layout de producción: repo real ro + worktree (subdir del repo) rw encima;
        el python del venv compartido sigue ejecutando dentro del sandbox."""
        import tempfile
        wt = tempfile.mkdtemp(prefix=".sandbox-test-", dir=PROJECT)
        try:
            sb = BubblewrapSandbox(wt, env_builder=lambda: {"PATH": "/usr/bin:/bin"},
                                   extra_ro_binds=[PROJECT])
            res = sb.run(f'"{os.path.join(PROJECT, "venv", "bin", "python")}" --version')
            assert res.returncode == 0, res.stderr
            assert "Python" in res.stdout
            # Y el worktree sigue siendo escribible
            res2 = sb.run("echo ok > marca.txt")
            assert res2.returncode == 0
            assert os.path.exists(os.path.join(wt, "marca.txt"))
        finally:
            _shutil_rmtree(wt)
