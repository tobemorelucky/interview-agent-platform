from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

def test_compatibility_bats_have_no_process_or_docker_logic():
    for name in ('start-dev.bat','stop-dev.bat','status-dev.bat'):
        text = (ROOT/name).read_text().lower()
        assert 'powershell.exe' in text
        assert 'taskkill' not in text and 'docker compose' not in text

def test_delivery_scripts_preserve_data_and_process_ownership():
    common = (ROOT/'scripts/platform-common.ps1').read_text()
    stop = (ROOT/'scripts/stop-interview-platform.ps1').read_text()
    assert 'CreationDate' in common and 'CommandLine' in common
    assert "Invoke-Compose @('stop')" in stop
    assert "Invoke-Compose @('down" not in stop
    assert 'Remove-Item' not in stop and 'taskkill' not in stop
    assert 'Stop-OwnedService' in stop

def test_demo_launcher_separates_database_broker_and_provider():
    text = (ROOT/'scripts/delivery.py').read_text()
    assert 'interview_agent_m6_f3_ux_demo' in text
    assert 'database="13"' in text and 'database="14"' in text
    assert 'DEMO_OUTBOUND_HTTP_BLOCKED' in text
    assert 'settings_module.settings = settings' in text

def test_launcher_reads_the_migration_head_from_the_scripts():
    # A pinned head made every migration after 0024 unable to start the
    # platform: the launcher refused a correctly migrated database.
    text = (ROOT/'scripts/delivery.py').read_text()
    assert '"0024"' not in text and "'0024'" not in text
    assert 'get_heads()' in text and 'REVISION_MISMATCH' in text
