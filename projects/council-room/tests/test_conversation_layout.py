from pathlib import Path
from bs4 import BeautifulSoup
from council_chamber.hecate import CONVERSATION_STYLE
from council_chamber.mercury import GUARD

ROOT=Path(__file__).resolve().parents[1]/'src/council_chamber'
def test_selection_and_send_controls_share_composer_dock():
    page=BeautifulSoup((ROOT/'room_static/index.html').read_text(),'html.parser')
    dock=page.select_one('#composer-dock')
    assert dock.select_one('#recipient-label') and dock.select_one('#message-form') and dock.select_one('#ask-hecate')
    assert not page.select_one('.conversation-heading').select_one('#recipient-label')
    assert len(dock.select('[name="reply-member"]'))==4
    assert dock.select_one('details#hecate-note summary').text=='Who sees this conversation?'

def test_runtime_style_retires_onboarding_without_weakening_honesty():
    assert 'onboarding instructions, already fulfilled' in CONVERSATION_STYLE
    assert 'Do not repeat your name' in CONVERSATION_STYLE
    assert 'never invent access or verification' in CONVERSATION_STYLE
    assert 'structured review format only when an actual review is requested' in GUARD
