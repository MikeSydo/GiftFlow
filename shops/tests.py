from unittest.mock import patch, MagicMock
from django.test import TestCase
from .services import CategoryMatcher, MatchResult, _normalise, HIGH_CONFIDENCE_THRESHOLD


# Helper to build a fake Category object
def _make_category(id_: int, name: str, slug: str) -> MagicMock:
    cat = MagicMock()
    cat.id = id_
    cat.name = name
    cat.slug = slug
    return cat


# Shared fixture - a small catalogue of canonical categories
FAKE_CATEGORIES = [
    _make_category(1, 'Electronics', 'electronics'),
    _make_category(2, 'Home & Kitchen', 'home'),
    _make_category(3, 'Sport & Outdoors', 'sport'),
    _make_category(4, 'Books', 'books'),
    _make_category(5, 'Toys & Kids', 'toys'),
    _make_category(6, 'Beauty & Health', 'beauty'),
    _make_category(7, 'Clothing', 'clothing'),
    _make_category(8, 'Experiences', 'experiences'),
]

# Build the list-of-tuples that _load_categories returns
FAKE_CACHE = [(c.id, c.name, c.slug, c) for c in FAKE_CATEGORIES]


# Tests
class NormaliseTestCase(TestCase):
    """Unit tests for the _normalise helper."""

    def test_lowercase(self):
        self.assertEqual(_normalise('Electronics'), 'electronics')

    def test_strips_punctuation(self):
        # '>' is replaced by space, then whitespace is collapsed, so double-space becomes single
        self.assertEqual(_normalise('Home >> Kitchen!'), 'home kitchen')

    def test_replaces_separators_with_space(self):
        result = _normalise('Sport/Outdoors')
        self.assertIn('sport', result)
        self.assertIn('outdoors', result)

    def test_unicode_accent_normalisation(self):
        # Cyrillic or accented Latin should be handled gracefully
        result = _normalise('Électronique')
        self.assertIn('electronique', result)

    def test_empty_string(self):
        self.assertEqual(_normalise(''), '')

    def test_whitespace_collapse(self):
        self.assertEqual(_normalise('  a   b  '), 'a b')


class CategoryMatcherTestCase(TestCase):
    """Unit tests for CategoryMatcher using a mocked DB cache."""

    def setUp(self):
        # Always start with a clean cache
        CategoryMatcher.invalidate_cache()

    def tearDown(self):
        CategoryMatcher.invalidate_cache()

    def _matcher(self, threshold=HIGH_CONFIDENCE_THRESHOLD) -> CategoryMatcher:
        return CategoryMatcher(threshold=threshold)

    # Synonym map tests
    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_synonym_hit_returns_confidence_100(self, _mock):
        result = self._matcher().match('laptop')
        self.assertIsInstance(result, MatchResult)
        self.assertEqual(result.confidence, 100.0)
        self.assertFalse(result.needs_review)
        self.assertEqual(result.category.slug, 'electronics')

    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_synonym_hit_is_case_insensitive(self, _mock):
        result = self._matcher().match('LAPTOP computer')
        self.assertEqual(result.confidence, 100.0)
        self.assertFalse(result.needs_review)

    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_synonym_hit_in_breadcrumb_string(self, _mock):
        """A typical breadcrumb like 'Home > Books > Fiction' should match books."""
        result = self._matcher().match('Home > Books > Fiction')
        self.assertFalse(result.needs_review)
        self.assertEqual(result.category.slug, 'books')

    # Fuzzy match tests
    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_exact_category_name_fuzzy_match(self, _mock):
        """Exact name → should score 100 via fuzzy even without synonym."""
        result = self._matcher().match('Electronics')
        self.assertFalse(result.needs_review)
        self.assertGreaterEqual(result.confidence, HIGH_CONFIDENCE_THRESHOLD)
        self.assertEqual(result.category.slug, 'electronics')

    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_partial_category_name_above_threshold(self, _mock):
        """'Toys' should map to 'Toys & Kids' above threshold."""
        result = self._matcher().match('Toys')
        self.assertFalse(result.needs_review)
        self.assertEqual(result.category.slug, 'toys')

    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_low_score_sets_needs_review(self, _mock):
        """A completely unrelated string should trigger needs_review."""
        result = self._matcher().match('xyzzy frobnicator 12345')
        self.assertTrue(result.needs_review)

    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_score_just_below_threshold_needs_review(self, _mock):
        """An extremely high threshold (100) forces ALL fuzzy matches to need_review."""
        # token_set_ratio can score 100 for substrings, so we need threshold > 100 to force failure
        # Use threshold=100.1 which is above the max possible score (100)
        result = self._matcher(threshold=100.1).match('Electronics')
        self.assertTrue(result.needs_review)

    # Edge cases
    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_empty_string_needs_review(self, _mock):
        result = self._matcher().match('')
        self.assertTrue(result.needs_review)
        self.assertIsNone(result.category)

    @patch.object(CategoryMatcher, '_load_categories', return_value=FAKE_CACHE)
    def test_whitespace_only_needs_review(self, _mock):
        result = self._matcher().match('   ')
        self.assertTrue(result.needs_review)

    @patch.object(CategoryMatcher, '_load_categories', return_value=[])
    def test_no_categories_in_db(self, _mock):
        """When the DB has no categories, needs_review is always True."""
        result = self._matcher().match('Electronics')
        self.assertTrue(result.needs_review)
        self.assertEqual(result.confidence, 0.0)

    # Cache tests
    def test_invalidate_cache_clears_state(self):
        CategoryMatcher._cache = FAKE_CACHE
        CategoryMatcher.invalidate_cache()
        self.assertIsNone(CategoryMatcher._cache)

    @patch.object(CategoryMatcher, '_fetch_from_db', return_value=FAKE_CACHE)
    def test_cache_is_loaded_once(self, mock_fetch):
        CategoryMatcher._cache = None
        matcher = self._matcher()
        matcher.match('Electronics')
        matcher.match('Books')
        mock_fetch.assert_called_once()
