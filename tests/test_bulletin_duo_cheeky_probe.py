from unittest.mock import patch

from news_prototype import bulletin_duo_cheeky_probe as cheeky
from news_prototype import bulletin_duo_content_probe as original


def test_probe_uses_new_style_only_and_restores_original_config(tmp_path):
    turns = [{'speaker': 'Бендер', 'text': 'Первая реплика.'},
             {'speaker': 'Киберсенсей', 'text': 'Вторая реплика.'}]
    old_voices = original.VOICES
    old_body = original.body_for(turns)

    def fake_run(job, folder):
        assert job == cheeky.JOB and folder == cheeky.FOLDER
        body = original.body_for(turns)
        before, after = old_body['contents'][0]['parts'], body['contents'][0]['parts']
        # Keep the listened-to comparison unchanged even when the experimental
        # bulletin's delivery instructions evolve.
        assert after[0]['speechMetadata']['style'] == cheeky.BENDER_STYLE
        assert 'ехидной интонацией' in cheeky.BENDER_STYLE
        assert after[0]['text'] == before[0]['text']
        assert after[1] == before[1]
        assert body['generationConfig'] == old_body['generationConfig']
        raise FileExistsError

    with patch.object(original, 'run', side_effect=fake_run) as run:
        assert cheeky.main() == 1
        run.assert_called_once()
    assert original.VOICES is old_voices
    assert original.body_for(turns) == old_body
