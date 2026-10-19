import pytest

from pingwin.pairing import Invite, PairingError, Reply, Syncthing

DEV = "ABCDEFG-HIJKLMN-OPQRSTU-VWXYZ23-4567ABC-DEFGHIJ-KLMNOPQ-RSTUVWX"


def test_invite_round_trip_and_tolerates_whitespace():
    code = Invite(DEV, "pingwin-settings", "Pingwin").code()
    assert code.startswith("PINGWIN-INVITE-")
    messy = code[:20] + "\n  " + code[20:] + " "
    assert Invite.parse(messy) == Invite(DEV, "pingwin-settings", "Pingwin")


def test_reply_round_trip():
    assert Reply.parse(Reply(DEV, "FRIENDS-PC").code()) == Reply(DEV, "FRIENDS-PC")


@pytest.mark.parametrize(
    "code",
    ["hello", "PINGWIN-INVITE-!!!", Reply(DEV, "X").code(), Invite("not-a-device", "f").code()],
)
def test_bad_invites_are_explained(code):
    with pytest.raises(PairingError):
        Invite.parse(code)


def test_bad_pc_name_rejected():
    with pytest.raises(PairingError, match="PC name"):
        Reply.parse(Reply(DEV, "bad/name").code())


def test_reads_syncthing_config(tmp_path):
    cfg = tmp_path / "config.xml"
    cfg.write_text(
        '<configuration><gui enabled="true" tls="true"><address>0.0.0.0:8385</address>'
        "<apikey>abc123</apikey></gui></configuration>",
        encoding="utf-8",
    )
    st = Syncthing.from_config(cfg)
    assert st.base_url == "https://127.0.0.1:8385" and st.api_key == "abc123"


def test_missing_syncthing_is_explained(tmp_path):
    with pytest.raises(PairingError, match="Couldn't read"):
        Syncthing.from_config(tmp_path / "nope.xml")


def test_pc_name_from_device_name():
    from pingwin.pairing import pc_name_for_device

    assert pc_name_for_device("Pingwin backup (FRIENDS-PC)") == "FRIENDS-PC"
    assert pc_name_for_device("BTM-BMAX") == "BTM-BMAX"
