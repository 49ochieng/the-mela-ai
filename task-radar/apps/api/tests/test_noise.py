from app.utils.noise import is_noise_email, is_noise_teams


def test_noise_email_noreply():
    assert is_noise_email(sender_email="no-reply@vendor.com", subject="Receipt", body="…")
    assert is_noise_email(sender_email="mailer-daemon@x.com", subject="Delivery failure", body="")


def test_noise_email_autoreply():
    assert is_noise_email(sender_email="boss@co.com",
                            subject="Out of office: vacation", body="I am out")


def test_legit_email_passes():
    assert not is_noise_email(sender_email="boss@co.com",
                                subject="Please review the report by Friday",
                                body="Could you take a look at the Q3 report and send feedback by Friday?")


def test_noise_teams_too_short_no_mention():
    assert is_noise_teams("ok thanks", mentions_user=False)
    assert not is_noise_teams("ok thanks", mentions_user=True)


def test_noise_teams_emoji_only():
    assert is_noise_teams("👍👍👍", mentions_user=False)
