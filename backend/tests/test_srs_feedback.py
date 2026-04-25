"""SRS feedback tests."""

from app.srs.feedback import PostGenerationFeedback


class TestPostGenerationFeedback:
    """Tests for PostGenerationFeedback collocation usage detection."""

    def test_only_used_collocations_marked_reviewed(self):
        feedback = PostGenerationFeedback()
        provided = ["dober dan", "hvala lepa", "prosim"]
        story_text = "V zgodbi je bilo dober dan in hvala lepa."
        used = feedback.find_used_collocations(provided, story_text)
        assert "dober dan" in used
        assert "hvala lepa" in used
        assert "prosim" not in used

    def test_empty_story_marks_nothing(self):
        feedback = PostGenerationFeedback()
        provided = ["dober dan", "hvala lepa"]
        used = feedback.find_used_collocations(provided, "")
        assert len(used) == 0

    def test_case_insensitive_matching(self):
        feedback = PostGenerationFeedback()
        provided = ["Dober Dan"]
        story_text = "V zgodbi je bilo dober dan."
        used = feedback.find_used_collocations(provided, story_text)
        assert "Dober Dan" in used
