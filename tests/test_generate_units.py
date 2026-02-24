"""
Tests for scripts/generate_units.py - Generic unit generator.

Covers all strategies (permutation, cross_product, direct), repeat/Monte Carlo
support, output functions, sanitization, and config extraction helpers.
Target: 80%+ line coverage.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from generate_units import (
    _sanitize_name,
    generate_cross_product_units,
    generate_direct_units,
    generate_permutation_units,
    generate_units,
    get_items_key,
    get_name_field,
    get_positions,
    get_repeat_count,
    get_strategy,
    load_items_data,
    load_yaml,
    log_info,
    main,
    write_units_chunked,
    write_units_to_file,
    write_units_to_stdout,
)


# =============================================================================
# Helpers
# =============================================================================

def write_yaml(path: Path, data):
    """Write data to a YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f)


# =============================================================================
# _sanitize_name
# =============================================================================

class TestSanitizeName:
    """Tests for _sanitize_name()."""

    def test_replaces_spaces_with_underscores(self):
        assert _sanitize_name("hello world") == "hello_world"

    def test_replaces_slashes_with_dashes(self):
        assert _sanitize_name("a/b/c") == "a-b-c"

    def test_replaces_both_spaces_and_slashes(self):
        assert _sanitize_name("foo bar/baz qux") == "foo_bar-baz_qux"

    def test_no_special_chars(self):
        assert _sanitize_name("simple") == "simple"

    def test_non_string_input(self):
        """Non-string input is converted via str()."""
        assert _sanitize_name(42) == "42"

    def test_empty_string(self):
        assert _sanitize_name("") == ""


# =============================================================================
# log_info
# =============================================================================

class TestLogInfo:
    """Tests for log_info()."""

    def test_writes_to_stderr(self, capsys):
        log_info("test message")
        captured = capsys.readouterr()
        assert captured.err.strip() == "test message"
        assert captured.out == ""


# =============================================================================
# load_yaml
# =============================================================================

class TestLoadYaml:
    """Tests for load_yaml()."""

    def test_loads_dict(self, tmp_path):
        p = tmp_path / "data.yaml"
        write_yaml(p, {"key": "value"})
        result = load_yaml(p)
        assert result == {"key": "value"}

    def test_loads_list(self, tmp_path):
        p = tmp_path / "data.yaml"
        write_yaml(p, [1, 2, 3])
        result = load_yaml(p)
        assert result == [1, 2, 3]


# =============================================================================
# load_items_data
# =============================================================================

class TestLoadItemsData:
    """Tests for load_items_data()."""

    def test_loads_yaml_relative_to_config(self, tmp_path):
        """Source path is resolved relative to config file location."""
        items = [{"name": "alpha"}, {"name": "beta"}]
        items_path = tmp_path / "pipeline" / "items.yaml"
        write_yaml(items_path, {"cards": items})

        config = {
            "processing": {
                "items": {"source": "items.yaml", "key": "cards"}
            }
        }
        config_path = tmp_path / "pipeline" / "config.yaml"

        result = load_items_data(config, config_path)
        assert result == {"cards": items}

    def test_raises_when_no_source(self, tmp_path):
        """ValueError when processing.items.source is missing."""
        config = {"processing": {"items": {}}}
        config_path = tmp_path / "config.yaml"
        with pytest.raises(ValueError, match="processing.items.source is required"):
            load_items_data(config, config_path)

    def test_raises_when_no_items_section(self, tmp_path):
        """ValueError when processing.items is missing entirely."""
        config = {"processing": {}}
        config_path = tmp_path / "config.yaml"
        with pytest.raises(ValueError, match="processing.items.source is required"):
            load_items_data(config, config_path)

    def test_raises_when_no_processing_section(self, tmp_path):
        """ValueError when processing section is missing."""
        config = {}
        config_path = tmp_path / "config.yaml"
        with pytest.raises(ValueError, match="processing.items.source is required"):
            load_items_data(config, config_path)

    def test_raises_file_not_found(self, tmp_path):
        """FileNotFoundError when source file doesn't exist."""
        config = {"processing": {"items": {"source": "missing.yaml"}}}
        config_path = tmp_path / "config.yaml"
        with pytest.raises(FileNotFoundError):
            load_items_data(config, config_path)


# =============================================================================
# get_strategy
# =============================================================================

class TestGetStrategy:
    """Tests for get_strategy()."""

    def test_returns_configured_strategy(self):
        config = {"processing": {"strategy": "cross_product"}}
        assert get_strategy(config) == "cross_product"

    def test_defaults_to_permutation(self):
        config = {"processing": {}}
        assert get_strategy(config) == "permutation"

    def test_defaults_when_no_processing(self):
        assert get_strategy({}) == "permutation"


# =============================================================================
# get_positions
# =============================================================================

class TestGetPositions:
    """Tests for get_positions()."""

    def test_dict_positions_with_name(self):
        config = {
            "processing": {
                "positions": [
                    {"name": "past_card"},
                    {"name": "present_card"},
                ]
            }
        }
        positions = get_positions(config)
        assert len(positions) == 2
        assert positions[0] == {"name": "past_card"}
        assert positions[1] == {"name": "present_card"}

    def test_string_positions_converted_to_dicts(self):
        config = {"processing": {"positions": ["slot_a", "slot_b"]}}
        positions = get_positions(config)
        assert positions == [{"name": "slot_a"}, {"name": "slot_b"}]

    def test_dict_position_missing_name_raises(self):
        config = {"processing": {"positions": [{"source_key": "cards"}]}}
        with pytest.raises(ValueError, match="must have a 'name' field"):
            get_positions(config)

    def test_invalid_position_format_raises(self):
        config = {"processing": {"positions": [123]}}
        with pytest.raises(ValueError, match="Invalid position format"):
            get_positions(config)

    def test_empty_positions(self):
        config = {"processing": {"positions": []}}
        assert get_positions(config) == []

    def test_no_positions_key(self):
        config = {"processing": {}}
        assert get_positions(config) == []

    def test_no_processing_key(self):
        assert get_positions({}) == []

    def test_dict_position_preserves_extra_fields(self):
        """Extra fields like source_key are preserved."""
        config = {
            "processing": {
                "positions": [{"name": "char", "source_key": "characters"}]
            }
        }
        positions = get_positions(config)
        assert positions[0] == {"name": "char", "source_key": "characters"}


# =============================================================================
# get_name_field
# =============================================================================

class TestGetNameField:
    """Tests for get_name_field()."""

    def test_returns_configured_name_field(self):
        config = {"processing": {"items": {"name_field": "id"}}}
        assert get_name_field(config) == "id"

    def test_defaults_to_name(self):
        config = {"processing": {"items": {}}}
        assert get_name_field(config) == "name"

    def test_defaults_when_no_items(self):
        config = {"processing": {}}
        assert get_name_field(config) == "name"

    def test_defaults_when_no_processing(self):
        assert get_name_field({}) == "name"


# =============================================================================
# get_items_key
# =============================================================================

class TestGetItemsKey:
    """Tests for get_items_key()."""

    def test_returns_configured_key(self):
        config = {"processing": {"items": {"key": "cards"}}}
        assert get_items_key(config) == "cards"

    def test_returns_none_when_no_key(self):
        config = {"processing": {"items": {}}}
        assert get_items_key(config) is None

    def test_returns_none_when_no_items(self):
        config = {"processing": {}}
        assert get_items_key(config) is None

    def test_returns_none_when_no_processing(self):
        assert get_items_key({}) is None


# =============================================================================
# get_repeat_count
# =============================================================================

class TestGetRepeatCount:
    """Tests for get_repeat_count()."""

    def test_returns_configured_repeat(self):
        config = {"processing": {"repeat": 100}}
        assert get_repeat_count(config) == 100

    def test_defaults_to_1(self):
        config = {"processing": {}}
        assert get_repeat_count(config) == 1

    def test_defaults_when_no_processing(self):
        assert get_repeat_count({}) == 1


# =============================================================================
# generate_permutation_units
# =============================================================================

class TestGeneratePermutationUnits:
    """Tests for generate_permutation_units()."""

    def _make_config(self, positions, name_field="name", items_key=None):
        """Build a minimal config dict for permutation strategy."""
        items_config = {"source": "items.yaml", "name_field": name_field}
        if items_key:
            items_config["key"] = items_key
        return {
            "processing": {
                "strategy": "permutation",
                "positions": positions,
                "items": items_config,
            }
        }

    def test_basic_permutations(self):
        """3 items across 2 positions = 3*2 = 6 permutations."""
        config = self._make_config(
            [{"name": "slot1"}, {"name": "slot2"}]
        )
        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        units = generate_permutation_units(config, items)
        assert len(units) == 6
        # Each unit has unit_id, slot1, slot2
        for u in units:
            assert "unit_id" in u
            assert "slot1" in u
            assert "slot2" in u

    def test_unit_ids_are_unique(self):
        config = self._make_config(
            [{"name": "a"}, {"name": "b"}]
        )
        items = [{"name": "X"}, {"name": "Y"}, {"name": "Z"}]
        units = generate_permutation_units(config, items)
        ids = [u["unit_id"] for u in units]
        assert len(ids) == len(set(ids))

    def test_unit_id_format(self):
        """Unit IDs join sanitized names with hyphens."""
        config = self._make_config(
            [{"name": "a"}, {"name": "b"}]
        )
        items = [{"name": "foo bar"}, {"name": "baz/qux"}]
        units = generate_permutation_units(config, items)
        assert len(units) == 2
        ids = {u["unit_id"] for u in units}
        assert "foo_bar-baz-qux" in ids
        assert "baz-qux-foo_bar" in ids

    def test_with_items_key(self):
        """Items extracted from source data via items_key."""
        config = self._make_config(
            [{"name": "a"}], items_key="cards"
        )
        items_data = {"cards": [{"name": "Ace"}, {"name": "King"}]}
        units = generate_permutation_units(config, items_data)
        assert len(units) == 2

    def test_items_key_not_found_raises(self):
        config = self._make_config(
            [{"name": "a"}], items_key="missing"
        )
        items_data = {"cards": [{"name": "Ace"}]}
        with pytest.raises(ValueError, match="Key 'missing' not found"):
            generate_permutation_units(config, items_data)

    def test_items_key_requires_dict_source(self):
        config = self._make_config(
            [{"name": "a"}], items_key="cards"
        )
        items_data = [{"name": "Ace"}]
        with pytest.raises(ValueError, match="Source file must be a dict"):
            generate_permutation_units(config, items_data)

    def test_items_must_be_list(self):
        config = self._make_config([{"name": "a"}])
        items_data = "not a list"
        with pytest.raises(ValueError, match="Items must be a list"):
            generate_permutation_units(config, items_data)

    def test_no_positions_raises(self):
        config = self._make_config([])
        items = [{"name": "A"}]
        with pytest.raises(ValueError, match="processing.positions is required"):
            generate_permutation_units(config, items)

    def test_not_enough_items_raises(self):
        """Fewer items than positions raises ValueError."""
        config = self._make_config(
            [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        )
        items = [{"name": "X"}, {"name": "Y"}]
        with pytest.raises(ValueError, match="Not enough items"):
            generate_permutation_units(config, items)

    def test_missing_name_field_raises(self):
        """Item without the configured name_field raises ValueError."""
        config = self._make_config(
            [{"name": "a"}], name_field="title"
        )
        items = [{"name": "X"}, {"name": "Y"}]
        with pytest.raises(ValueError, match="Item missing required field 'title'"):
            generate_permutation_units(config, items)

    def test_duplicate_item_names_raises(self):
        """Items with duplicate name_field values raise ValueError."""
        config = self._make_config(
            [{"name": "a"}, {"name": "b"}]
        )
        items = [{"name": "X"}, {"name": "X"}, {"name": "Y"}]
        with pytest.raises(ValueError, match="Duplicate unit_id detected"):
            generate_permutation_units(config, items)

    def test_limit_reduces_items(self):
        """Limit restricts item pool before generating permutations."""
        config = self._make_config(
            [{"name": "a"}, {"name": "b"}]
        )
        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}]
        # Without limit: 4*3 = 12 permutations
        units_full = generate_permutation_units(config, items)
        assert len(units_full) == 12
        # With limit=3: 3*2 = 6 permutations
        units_limited = generate_permutation_units(config, items, limit=3)
        assert len(units_limited) == 6

    def test_single_position(self):
        """Single position with N items = N permutations."""
        config = self._make_config([{"name": "slot"}])
        items = [{"name": "A"}, {"name": "B"}]
        units = generate_permutation_units(config, items)
        assert len(units) == 2


# =============================================================================
# generate_cross_product_units
# =============================================================================

class TestGenerateCrossProductUnits:
    """Tests for generate_cross_product_units()."""

    def _make_config(self, positions, name_field="name"):
        return {
            "processing": {
                "strategy": "cross_product",
                "positions": positions,
                "items": {"source": "data.yaml", "name_field": name_field},
            }
        }

    def test_basic_cross_product(self):
        """2 characters x 2 situations = 4 units."""
        config = self._make_config([
            {"name": "character", "source_key": "characters"},
            {"name": "situation", "source_key": "situations"},
        ])
        items_data = {
            "characters": [{"name": "Alice"}, {"name": "Bob"}],
            "situations": [{"name": "rain"}, {"name": "sun"}],
        }
        units = generate_cross_product_units(config, items_data)
        assert len(units) == 4
        for u in units:
            assert "unit_id" in u
            assert "character" in u
            assert "situation" in u

    def test_unique_unit_ids(self):
        config = self._make_config([
            {"name": "a", "source_key": "xs"},
            {"name": "b", "source_key": "ys"},
        ])
        items_data = {
            "xs": [{"name": "X1"}, {"name": "X2"}],
            "ys": [{"name": "Y1"}, {"name": "Y2"}],
        }
        units = generate_cross_product_units(config, items_data)
        ids = [u["unit_id"] for u in units]
        assert len(ids) == len(set(ids))

    def test_source_key_fallback_to_name_plus_s(self):
        """When no source_key, falls back to position name + 's'."""
        config = self._make_config([
            {"name": "character"},
            {"name": "situation"},
        ])
        items_data = {
            "characters": [{"name": "Alice"}],
            "situations": [{"name": "rain"}],
        }
        units = generate_cross_product_units(config, items_data)
        assert len(units) == 1

    def test_missing_source_key_raises(self):
        config = self._make_config([
            {"name": "character", "source_key": "chars"},
        ])
        items_data = {"characters": [{"name": "Alice"}]}
        with pytest.raises(ValueError, match="Missing 'chars' in source file"):
            generate_cross_product_units(config, items_data)

    def test_non_list_items_raises(self):
        config = self._make_config([
            {"name": "character", "source_key": "characters"},
        ])
        items_data = {"characters": "not a list"}
        with pytest.raises(ValueError, match="must be a list"):
            generate_cross_product_units(config, items_data)

    def test_non_dict_source_raises(self):
        config = self._make_config([
            {"name": "character", "source_key": "characters"},
        ])
        items_data = [{"name": "Alice"}]
        with pytest.raises(ValueError, match="Source file must be a dict"):
            generate_cross_product_units(config, items_data)

    def test_no_positions_raises(self):
        config = self._make_config([])
        items_data = {}
        with pytest.raises(ValueError, match="processing.positions is required"):
            generate_cross_product_units(config, items_data)

    def test_missing_name_field_raises(self):
        """Item without any usable name field raises ValueError."""
        config = self._make_config(
            [{"name": "x", "source_key": "xs"}],
            name_field="title"
        )
        items_data = {"xs": [{"other": "val"}]}
        with pytest.raises(ValueError, match="Item missing field for unit_id"):
            generate_cross_product_units(config, items_data)

    def test_fallback_to_id_field(self):
        """When name_field is missing, cross_product falls back to 'id' then 'name'."""
        config = self._make_config(
            [{"name": "x", "source_key": "xs"}],
            name_field="title"
        )
        items_data = {"xs": [{"id": "item1"}]}
        units = generate_cross_product_units(config, items_data)
        assert len(units) == 1
        assert units[0]["unit_id"] == "item1"

    def test_fallback_to_name_field(self):
        """Falls back to 'name' when name_field and 'id' are both missing."""
        config = self._make_config(
            [{"name": "x", "source_key": "xs"}],
            name_field="title"
        )
        items_data = {"xs": [{"name": "item1"}]}
        units = generate_cross_product_units(config, items_data)
        assert len(units) == 1
        assert units[0]["unit_id"] == "item1"

    def test_limit_applied_per_group(self):
        config = self._make_config([
            {"name": "a", "source_key": "xs"},
            {"name": "b", "source_key": "ys"},
        ])
        items_data = {
            "xs": [{"name": "X1"}, {"name": "X2"}, {"name": "X3"}],
            "ys": [{"name": "Y1"}, {"name": "Y2"}, {"name": "Y3"}],
        }
        # Without limit: 3*3 = 9
        units_full = generate_cross_product_units(config, items_data)
        assert len(units_full) == 9
        # With limit=2: 2*2 = 4
        units_limited = generate_cross_product_units(config, items_data, limit=2)
        assert len(units_limited) == 4

    def test_duplicate_unit_id_raises(self):
        """Duplicate unit IDs across cross product raise ValueError."""
        config = self._make_config([
            {"name": "a", "source_key": "xs"},
            {"name": "b", "source_key": "ys"},
        ])
        # Both items have same name in both groups, so "A-A" appears twice? No --
        # cross product of [A] x [A] = (A,A) once. Need a scenario where
        # names collide. Two items with same name in same group can't happen
        # unless items in one group have the same resulting sanitized name.
        items_data = {
            "xs": [{"name": "A"}, {"name": "A"}],
            "ys": [{"name": "B"}],
        }
        with pytest.raises(ValueError, match="Duplicate unit_id detected"):
            generate_cross_product_units(config, items_data)


# =============================================================================
# generate_direct_units
# =============================================================================

class TestGenerateDirectUnits:
    """Tests for generate_direct_units()."""

    def _make_config(self, name_field="name", items_key=None, positions=None):
        items_config = {"source": "items.yaml", "name_field": name_field}
        if items_key:
            items_config["key"] = items_key
        cfg = {
            "processing": {
                "strategy": "direct",
                "items": items_config,
            }
        }
        if positions is not None:
            cfg["processing"]["positions"] = positions
        return cfg

    def test_basic_direct(self):
        """Each item becomes one unit."""
        config = self._make_config()
        items = [{"name": "Alice"}, {"name": "Bob"}]
        units = generate_direct_units(config, items)
        assert len(units) == 2
        assert units[0]["unit_id"] == "Alice"
        assert units[1]["unit_id"] == "Bob"

    def test_copies_all_top_level_data(self):
        """All item keys are copied into the unit."""
        config = self._make_config()
        items = [{"name": "Alice", "age": 30, "role": "warrior"}]
        units = generate_direct_units(config, items)
        assert units[0]["age"] == 30
        assert units[0]["role"] == "warrior"

    def test_with_positions_maps_item_keys(self):
        """Positions map item keys to position names."""
        config = self._make_config(
            positions=[{"name": "character"}, {"name": "weapon"}]
        )
        items = [
            {"name": "Alice", "character": {"class": "mage"}, "weapon": {"type": "staff"}}
        ]
        units = generate_direct_units(config, items)
        assert units[0]["character"] == {"class": "mage"}
        assert units[0]["weapon"] == {"type": "staff"}

    def test_no_positions_is_ok(self):
        """Direct strategy works without positions."""
        config = self._make_config()
        items = [{"name": "X"}]
        units = generate_direct_units(config, items)
        assert len(units) == 1

    def test_with_items_key(self):
        config = self._make_config(items_key="scenarios")
        items_data = {"scenarios": [{"name": "S1"}, {"name": "S2"}]}
        units = generate_direct_units(config, items_data)
        assert len(units) == 2

    def test_items_key_not_found_raises(self):
        config = self._make_config(items_key="missing")
        items_data = {"scenarios": [{"name": "S1"}]}
        with pytest.raises(ValueError, match="Key 'missing' not found"):
            generate_direct_units(config, items_data)

    def test_items_key_requires_dict_source(self):
        config = self._make_config(items_key="cards")
        items_data = [{"name": "Ace"}]
        with pytest.raises(ValueError, match="Source file must be a dict"):
            generate_direct_units(config, items_data)

    def test_items_must_be_list(self):
        config = self._make_config()
        items_data = "not a list"
        with pytest.raises(ValueError, match="Items must be a list"):
            generate_direct_units(config, items_data)

    def test_missing_name_field_raises(self):
        config = self._make_config(name_field="title")
        items = [{"other": "val"}]
        with pytest.raises(ValueError, match="Item missing field for unit_id"):
            generate_direct_units(config, items)

    def test_fallback_to_id(self):
        """Falls back to 'id' when name_field is missing."""
        config = self._make_config(name_field="title")
        items = [{"id": "item_1"}]
        units = generate_direct_units(config, items)
        assert units[0]["unit_id"] == "item_1"

    def test_fallback_to_name(self):
        """Falls back to 'name' when both name_field and 'id' are missing."""
        config = self._make_config(name_field="title")
        items = [{"name": "item_1"}]
        units = generate_direct_units(config, items)
        assert units[0]["unit_id"] == "item_1"

    def test_duplicate_unit_ids_raises(self):
        config = self._make_config()
        items = [{"name": "dup"}, {"name": "dup"}]
        with pytest.raises(ValueError, match="Duplicate unit_id detected"):
            generate_direct_units(config, items)

    def test_limit_applied(self):
        config = self._make_config()
        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        units = generate_direct_units(config, items, limit=2)
        assert len(units) == 2

    def test_sanitized_unit_ids(self):
        """Unit IDs are sanitized."""
        config = self._make_config()
        items = [{"name": "foo bar"}, {"name": "a/b"}]
        units = generate_direct_units(config, items)
        assert units[0]["unit_id"] == "foo_bar"
        assert units[1]["unit_id"] == "a-b"

    def test_position_key_not_in_item_is_skipped(self):
        """Position names not present in item are simply not added."""
        config = self._make_config(
            positions=[{"name": "character"}]
        )
        items = [{"name": "Alice", "weapon": "sword"}]
        units = generate_direct_units(config, items)
        assert "character" not in units[0]
        assert units[0]["weapon"] == "sword"

    def test_position_keys_not_duplicated_in_top_level_copy(self):
        """Keys already mapped via positions are not duplicated by top-level copy."""
        config = self._make_config(
            positions=[{"name": "role"}]
        )
        items = [{"name": "Alice", "role": "mage", "extra": "data"}]
        units = generate_direct_units(config, items)
        # role should be set from position mapping
        assert units[0]["role"] == "mage"
        # extra gets copied via top-level loop
        assert units[0]["extra"] == "data"


# =============================================================================
# generate_units (dispatcher + repeat)
# =============================================================================

class TestGenerateUnits:
    """Tests for generate_units() dispatcher and repeat functionality."""

    def _make_config(self, strategy="direct", repeat=1, positions=None, items_key=None):
        items_config = {"source": "items.yaml", "name_field": "name"}
        if items_key:
            items_config["key"] = items_key
        cfg = {
            "processing": {
                "strategy": strategy,
                "items": items_config,
            }
        }
        if repeat != 1:
            cfg["processing"]["repeat"] = repeat
        if positions is not None:
            cfg["processing"]["positions"] = positions
        return cfg

    def test_dispatches_to_direct(self):
        config = self._make_config(strategy="direct")
        items = [{"name": "A"}, {"name": "B"}]
        units = generate_units(config, items)
        assert len(units) == 2

    def test_dispatches_to_permutation(self):
        config = self._make_config(
            strategy="permutation",
            positions=[{"name": "x"}, {"name": "y"}]
        )
        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        units = generate_units(config, items)
        assert len(units) == 6  # 3P2 = 6

    def test_dispatches_to_cross_product(self):
        config = self._make_config(
            strategy="cross_product",
            positions=[
                {"name": "a", "source_key": "xs"},
                {"name": "b", "source_key": "ys"},
            ]
        )
        items_data = {
            "xs": [{"name": "X1"}],
            "ys": [{"name": "Y1"}, {"name": "Y2"}],
        }
        units = generate_units(config, items_data)
        assert len(units) == 2

    def test_unknown_strategy_raises(self):
        config = self._make_config(strategy="chaos")
        items = [{"name": "A"}]
        with pytest.raises(ValueError, match="Unknown strategy: 'chaos'"):
            generate_units(config, items)

    def test_repeat_count_1_no_repetition(self):
        """repeat=1 returns base units unchanged."""
        config = self._make_config(strategy="direct", repeat=1)
        items = [{"name": "A"}]
        units = generate_units(config, items)
        assert len(units) == 1
        assert "_repetition_id" not in units[0]

    def test_repeat_multiplies_units(self):
        """repeat=3 creates 3x the base units."""
        config = self._make_config(strategy="direct", repeat=3)
        items = [{"name": "A"}, {"name": "B"}]
        units = generate_units(config, items)
        assert len(units) == 6  # 2 base * 3 reps

    def test_repeat_unit_ids_include_rep_suffix(self):
        config = self._make_config(strategy="direct", repeat=2)
        items = [{"name": "A"}]
        units = generate_units(config, items)
        assert len(units) == 2
        assert units[0]["unit_id"] == "A__rep0000"
        assert units[1]["unit_id"] == "A__rep0001"

    def test_repeat_adds_repetition_fields(self):
        config = self._make_config(strategy="direct", repeat=2)
        items = [{"name": "A"}]
        units = generate_units(config, items)
        assert units[0]["_repetition_id"] == 0
        assert units[1]["_repetition_id"] == 1
        assert "_repetition_seed" in units[0]
        assert "_repetition_seed" in units[1]

    def test_repeat_seeds_are_deterministic(self):
        """Same input produces same seeds each time."""
        config = self._make_config(strategy="direct", repeat=3)
        items = [{"name": "TestItem"}]
        units1 = generate_units(config, items)
        units2 = generate_units(config, items)
        for u1, u2 in zip(units1, units2):
            assert u1["_repetition_seed"] == u2["_repetition_seed"]

    def test_repeat_seeds_differ_across_reps(self):
        """Different repetitions get different seeds."""
        config = self._make_config(strategy="direct", repeat=3)
        items = [{"name": "X"}]
        units = generate_units(config, items)
        seeds = [u["_repetition_seed"] for u in units]
        assert len(set(seeds)) == 3

    def test_repeat_interleaved_ordering(self):
        """Interleaved: item1_rep0, item2_rep0, item1_rep1, item2_rep1."""
        config = self._make_config(strategy="direct", repeat=2)
        items = [{"name": "A"}, {"name": "B"}]
        units = generate_units(config, items)
        assert len(units) == 4
        # rep0 first
        assert units[0]["unit_id"] == "A__rep0000"
        assert units[1]["unit_id"] == "B__rep0000"
        # then rep1
        assert units[2]["unit_id"] == "A__rep0001"
        assert units[3]["unit_id"] == "B__rep0001"

    def test_repeat_with_limit(self):
        """Limit is passed through to the underlying strategy."""
        config = self._make_config(
            strategy="permutation",
            repeat=2,
            positions=[{"name": "x"}]
        )
        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        units = generate_units(config, items, limit=2)
        # limit=2 items, 1 position: 2 base units * 2 reps = 4
        assert len(units) == 4


# =============================================================================
# write_units_to_file
# =============================================================================

class TestWriteUnitsToFile:
    """Tests for write_units_to_file()."""

    def test_writes_jsonl(self, tmp_path):
        units = [{"unit_id": "a"}, {"unit_id": "b"}]
        out = tmp_path / "output.jsonl"
        write_units_to_file(units, out)
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"unit_id": "a"}
        assert json.loads(lines[1]) == {"unit_id": "b"}

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "deep" / "nested" / "output.jsonl"
        write_units_to_file([{"unit_id": "x"}], out)
        assert out.exists()

    def test_empty_units(self, tmp_path):
        out = tmp_path / "empty.jsonl"
        write_units_to_file([], out)
        assert out.read_text() == ""


# =============================================================================
# write_units_chunked
# =============================================================================

class TestWriteUnitsChunked:
    """Tests for write_units_chunked()."""

    def test_single_chunk(self, tmp_path):
        out_dir = tmp_path / "chunks"
        units = [{"unit_id": f"u{i}"} for i in range(5)]
        num_chunks = write_units_chunked(units, out_dir, chunk_size=10)
        assert num_chunks == 1
        chunk_file = out_dir / "chunk_000" / "units.jsonl"
        assert chunk_file.exists()
        lines = chunk_file.read_text().strip().split("\n")
        assert len(lines) == 5

    def test_multiple_chunks(self, tmp_path):
        out_dir = tmp_path / "chunks"
        units = [{"unit_id": f"u{i}"} for i in range(7)]
        num_chunks = write_units_chunked(units, out_dir, chunk_size=3)
        assert num_chunks == 3
        # chunk_000: 3 units, chunk_001: 3 units, chunk_002: 1 unit
        assert (out_dir / "chunk_000" / "units.jsonl").exists()
        assert (out_dir / "chunk_001" / "units.jsonl").exists()
        assert (out_dir / "chunk_002" / "units.jsonl").exists()

        lines_2 = (out_dir / "chunk_002" / "units.jsonl").read_text().strip().split("\n")
        assert len(lines_2) == 1

    def test_exact_chunk_boundary(self, tmp_path):
        out_dir = tmp_path / "chunks"
        units = [{"unit_id": f"u{i}"} for i in range(6)]
        num_chunks = write_units_chunked(units, out_dir, chunk_size=3)
        assert num_chunks == 2

    def test_creates_parent_dirs(self, tmp_path):
        out_dir = tmp_path / "deep" / "nested" / "chunks"
        write_units_chunked([{"unit_id": "x"}], out_dir, chunk_size=10)
        assert (out_dir / "chunk_000" / "units.jsonl").exists()

    def test_empty_units(self, tmp_path):
        out_dir = tmp_path / "chunks"
        num_chunks = write_units_chunked([], out_dir, chunk_size=10)
        assert num_chunks == 0


# =============================================================================
# write_units_to_stdout
# =============================================================================

class TestWriteUnitsToStdout:
    """Tests for write_units_to_stdout()."""

    def test_writes_jsonl_to_stdout(self, capsys):
        units = [{"unit_id": "a", "x": 1}, {"unit_id": "b", "x": 2}]
        write_units_to_stdout(units)
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"unit_id": "a", "x": 1}
        assert json.loads(lines[1]) == {"unit_id": "b", "x": 2}

    def test_empty_units(self, capsys):
        write_units_to_stdout([])
        captured = capsys.readouterr()
        assert captured.out == ""


# =============================================================================
# Integration: load_items_data + generate_units end-to-end
# =============================================================================

class TestEndToEnd:
    """Integration tests using YAML files on disk."""

    def test_permutation_end_to_end(self, tmp_path):
        """Full flow: YAML file -> load -> permutation -> units."""
        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        items_path = tmp_path / "items.yaml"
        write_yaml(items_path, {"cards": items})

        config = {
            "processing": {
                "strategy": "permutation",
                "positions": [{"name": "left"}, {"name": "right"}],
                "items": {"source": "items.yaml", "key": "cards", "name_field": "name"},
            }
        }
        config_path = tmp_path / "config.yaml"

        items_data = load_items_data(config, config_path)
        units = generate_units(config, items_data)
        assert len(units) == 6  # 3P2

    def test_cross_product_end_to_end(self, tmp_path):
        """Full flow: YAML file -> load -> cross_product -> units."""
        data = {
            "characters": [{"name": "Alice"}, {"name": "Bob"}],
            "situations": [{"name": "rain"}, {"name": "sun"}, {"name": "fog"}],
        }
        data_path = tmp_path / "data.yaml"
        write_yaml(data_path, data)

        config = {
            "processing": {
                "strategy": "cross_product",
                "positions": [
                    {"name": "character", "source_key": "characters"},
                    {"name": "situation", "source_key": "situations"},
                ],
                "items": {"source": "data.yaml", "name_field": "name"},
            }
        }
        config_path = tmp_path / "config.yaml"

        items_data = load_items_data(config, config_path)
        units = generate_units(config, items_data)
        assert len(units) == 6  # 2 * 3

    def test_direct_with_repeat_end_to_end(self, tmp_path):
        """Full flow: YAML -> load -> direct + repeat -> units with seeds."""
        items_path = tmp_path / "items.yaml"
        write_yaml(items_path, {"scenarios": [{"id": "S1"}, {"id": "S2"}]})

        config = {
            "processing": {
                "strategy": "direct",
                "repeat": 5,
                "items": {"source": "items.yaml", "key": "scenarios", "name_field": "id"},
            }
        }
        config_path = tmp_path / "config.yaml"

        items_data = load_items_data(config, config_path)
        units = generate_units(config, items_data)
        assert len(units) == 10  # 2 * 5
        # Verify interleaving
        assert units[0]["unit_id"] == "S1__rep0000"
        assert units[1]["unit_id"] == "S2__rep0000"
        assert units[2]["unit_id"] == "S1__rep0001"

    def test_write_and_read_roundtrip(self, tmp_path):
        """Write units to file, read back, and verify."""
        config = {
            "processing": {
                "strategy": "direct",
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items = [{"name": "Alpha", "value": 42}, {"name": "Beta", "value": 99}]
        units = generate_units(config, items)

        out_path = tmp_path / "units.jsonl"
        write_units_to_file(units, out_path)

        read_back = []
        with open(out_path) as f:
            for line in f:
                read_back.append(json.loads(line))

        assert len(read_back) == 2
        assert read_back[0]["unit_id"] == "Alpha"
        assert read_back[0]["value"] == 42

    def test_chunked_write_roundtrip(self, tmp_path):
        """Write chunked, read back all chunks."""
        config = {
            "processing": {
                "strategy": "direct",
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items = [{"name": f"item_{i}"} for i in range(10)]
        units = generate_units(config, items)

        out_dir = tmp_path / "chunks"
        num_chunks = write_units_chunked(units, out_dir, chunk_size=3)
        assert num_chunks == 4  # ceil(10/3) = 4

        all_units = []
        for chunk_idx in range(num_chunks):
            chunk_file = out_dir / f"chunk_{chunk_idx:03d}" / "units.jsonl"
            with open(chunk_file) as f:
                for line in f:
                    all_units.append(json.loads(line))

        assert len(all_units) == 10


# =============================================================================
# Edge cases and additional branch coverage
# =============================================================================

class TestEdgeCases:
    """Edge cases for additional branch coverage."""

    def test_permutation_with_no_items_key_uses_raw_data(self):
        """When no items_key, the raw items_data list is used directly."""
        config = {
            "processing": {
                "strategy": "permutation",
                "positions": [{"name": "a"}],
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items = [{"name": "X"}, {"name": "Y"}]
        units = generate_permutation_units(config, items)
        assert len(units) == 2

    def test_direct_with_no_items_key_uses_raw_list(self):
        """When no items_key, the raw list is used directly."""
        config = {
            "processing": {
                "strategy": "direct",
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items = [{"name": "Solo"}]
        units = generate_direct_units(config, items)
        assert len(units) == 1

    def test_permutation_items_key_with_non_list_value(self):
        """items_key pointing to a non-list value raises."""
        config = {
            "processing": {
                "strategy": "permutation",
                "positions": [{"name": "a"}],
                "items": {"source": "items.yaml", "key": "data", "name_field": "name"},
            }
        }
        items_data = {"data": "not_a_list"}
        with pytest.raises(ValueError, match="Items must be a list"):
            generate_permutation_units(config, items_data)

    def test_direct_items_key_with_non_list_value(self):
        """items_key pointing to a non-list value raises."""
        config = {
            "processing": {
                "strategy": "direct",
                "items": {"source": "items.yaml", "key": "data", "name_field": "name"},
            }
        }
        items_data = {"data": "not_a_list"}
        with pytest.raises(ValueError, match="Items must be a list"):
            generate_direct_units(config, items_data)

    def test_repeat_seed_is_positive_integer(self):
        """Seeds are masked to 31-bit positive integers."""
        config = {
            "processing": {
                "strategy": "direct",
                "repeat": 2,
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items = [{"name": "A"}]
        units = generate_units(config, items)
        for u in units:
            assert u["_repetition_seed"] >= 0

    def test_generate_units_passes_limit_to_permutation(self):
        config = {
            "processing": {
                "strategy": "permutation",
                "positions": [{"name": "x"}],
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        units = generate_units(config, items, limit=2)
        assert len(units) == 2

    def test_generate_units_passes_limit_to_cross_product(self):
        config = {
            "processing": {
                "strategy": "cross_product",
                "positions": [
                    {"name": "x", "source_key": "xs"},
                    {"name": "y", "source_key": "ys"},
                ],
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items_data = {
            "xs": [{"name": "X1"}, {"name": "X2"}, {"name": "X3"}],
            "ys": [{"name": "Y1"}, {"name": "Y2"}, {"name": "Y3"}],
        }
        units = generate_units(config, items_data, limit=2)
        assert len(units) == 4  # 2*2

    def test_generate_units_passes_limit_to_direct(self):
        config = {
            "processing": {
                "strategy": "direct",
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        units = generate_units(config, items, limit=2)
        assert len(units) == 2

    def test_permutation_name_field_empty_string_raises(self):
        """Item with empty name_field value raises."""
        config = {
            "processing": {
                "strategy": "permutation",
                "positions": [{"name": "a"}],
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        items = [{"name": ""}]
        with pytest.raises(ValueError, match="Item missing required field 'name'"):
            generate_permutation_units(config, items)

    def test_direct_name_field_empty_string_raises(self):
        """Item with all possible name fields empty raises."""
        config = {
            "processing": {
                "strategy": "direct",
                "items": {"source": "items.yaml", "name_field": "title"},
            }
        }
        items = [{"title": "", "id": "", "name": ""}]
        with pytest.raises(ValueError, match="Item missing field for unit_id"):
            generate_direct_units(config, items)

    def test_cross_product_name_field_empty_raises(self):
        """Item with all possible name fields empty raises."""
        config = {
            "processing": {
                "strategy": "cross_product",
                "positions": [{"name": "x", "source_key": "xs"}],
                "items": {"source": "items.yaml", "name_field": "title"},
            }
        }
        items_data = {"xs": [{"title": "", "id": "", "name": ""}]}
        with pytest.raises(ValueError, match="Item missing field for unit_id"):
            generate_cross_product_units(config, items_data)

    def test_mixed_position_types(self):
        """Mix of string and dict positions work together."""
        config = {
            "processing": {
                "positions": [
                    "slot_a",
                    {"name": "slot_b"},
                ]
            }
        }
        positions = get_positions(config)
        assert positions == [{"name": "slot_a"}, {"name": "slot_b"}]


# =============================================================================
# main() CLI tests
# =============================================================================

class TestMainCLI:
    """Tests for the main() CLI entry point."""

    def _write_config_and_items(self, tmp_path, config_data=None, items_data=None):
        """Helper: write config.yaml and items.yaml in tmp_path."""
        if items_data is None:
            items_data = [{"name": "A"}, {"name": "B"}]
        if config_data is None:
            config_data = {
                "processing": {
                    "strategy": "direct",
                    "items": {"source": "items.yaml", "name_field": "name"},
                }
            }
        config_path = tmp_path / "config.yaml"
        items_path = tmp_path / "items.yaml"
        write_yaml(config_path, config_data)
        write_yaml(items_path, items_data)
        return config_path

    def test_output_to_file(self, tmp_path, monkeypatch, capsys):
        """main() with --output writes JSONL to file."""
        config_path = self._write_config_and_items(tmp_path)
        out_file = tmp_path / "output.jsonl"
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(config_path), "--output", str(out_file), "--quiet"],
        )
        main()
        assert out_file.exists()
        lines = out_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_output_to_stdout(self, tmp_path, monkeypatch, capsys):
        """main() without --output or --output-dir writes to stdout."""
        config_path = self._write_config_and_items(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(config_path), "--quiet"],
        )
        main()
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) == 2

    def test_output_dir_chunked(self, tmp_path, monkeypatch, capsys):
        """main() with --output-dir writes chunked output."""
        config_path = self._write_config_and_items(tmp_path)
        out_dir = tmp_path / "chunks"
        monkeypatch.setattr(
            "sys.argv",
            [
                "generate_units.py",
                "--config", str(config_path),
                "--output-dir", str(out_dir),
                "--chunk-size", "1",
                "--quiet",
            ],
        )
        main()
        assert (out_dir / "chunk_000" / "units.jsonl").exists()
        assert (out_dir / "chunk_001" / "units.jsonl").exists()

    def test_both_output_and_output_dir_exits(self, tmp_path, monkeypatch, capsys):
        """main() exits with error when both --output and --output-dir specified."""
        config_path = self._write_config_and_items(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "generate_units.py",
                "--config", str(config_path),
                "--output", str(tmp_path / "out.jsonl"),
                "--output-dir", str(tmp_path / "chunks"),
            ],
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_config_not_found_exits(self, tmp_path, monkeypatch, capsys):
        """main() exits with error when config file missing."""
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(tmp_path / "missing.yaml")],
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_source_file_not_found_exits(self, tmp_path, monkeypatch, capsys):
        """main() exits when source items file is missing."""
        config_data = {
            "processing": {
                "strategy": "direct",
                "items": {"source": "nonexistent.yaml", "name_field": "name"},
            }
        }
        config_path = tmp_path / "config.yaml"
        write_yaml(config_path, config_data)
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(config_path)],
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_items_source_missing_in_config_exits(self, tmp_path, monkeypatch, capsys):
        """main() exits when processing.items.source is missing from config."""
        config_data = {"processing": {"items": {}}}
        config_path = tmp_path / "config.yaml"
        write_yaml(config_path, config_data)
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(config_path)],
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_invalid_strategy_exits(self, tmp_path, monkeypatch, capsys):
        """main() exits when strategy is unknown."""
        config_data = {
            "processing": {
                "strategy": "invalid",
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        config_path = self._write_config_and_items(tmp_path, config_data=config_data)
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(config_path), "--quiet"],
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_max_units_limits_output(self, tmp_path, monkeypatch, capsys):
        """main() with --max-units limits the number of output units."""
        items = [{"name": f"item_{i}"} for i in range(10)]
        config_path = self._write_config_and_items(tmp_path, items_data=items)
        out_file = tmp_path / "output.jsonl"
        monkeypatch.setattr(
            "sys.argv",
            [
                "generate_units.py",
                "--config", str(config_path),
                "--output", str(out_file),
                "--max-units", "3",
                "--quiet",
            ],
        )
        main()
        lines = out_file.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_summary_output_not_quiet(self, tmp_path, monkeypatch, capsys):
        """main() prints summary to stderr when not --quiet."""
        config_path = self._write_config_and_items(tmp_path)
        out_file = tmp_path / "output.jsonl"
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(config_path), "--output", str(out_file)],
        )
        main()
        captured = capsys.readouterr()
        summary = json.loads(captured.err.strip())
        assert "summary" in summary
        assert summary["summary"]["strategy"] == "direct"
        assert summary["summary"]["total_units"] == 2

    def test_summary_with_repeat(self, tmp_path, monkeypatch, capsys):
        """Summary includes repeat info when repeat > 1."""
        config_data = {
            "processing": {
                "strategy": "direct",
                "repeat": 3,
                "items": {"source": "items.yaml", "name_field": "name"},
            }
        }
        config_path = self._write_config_and_items(tmp_path, config_data=config_data)
        out_file = tmp_path / "output.jsonl"
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(config_path), "--output", str(out_file)],
        )
        main()
        captured = capsys.readouterr()
        summary = json.loads(captured.err.strip())
        assert summary["summary"]["repeat"] == 3
        assert summary["summary"]["base_units"] == 2
        assert summary["summary"]["total_units"] == 6

    def test_summary_with_chunked_output(self, tmp_path, monkeypatch, capsys):
        """Summary includes chunk info when --output-dir is used."""
        config_path = self._write_config_and_items(tmp_path)
        out_dir = tmp_path / "chunks"
        monkeypatch.setattr(
            "sys.argv",
            [
                "generate_units.py",
                "--config", str(config_path),
                "--output-dir", str(out_dir),
                "--chunk-size", "1",
            ],
        )
        main()
        captured = capsys.readouterr()
        summary = json.loads(captured.err.strip())
        assert summary["summary"]["chunks"] == 2
        assert summary["summary"]["chunk_size"] == 1

    def test_summary_with_max_units(self, tmp_path, monkeypatch, capsys):
        """Summary includes max_units_applied when --max-units is used."""
        items = [{"name": f"item_{i}"} for i in range(10)]
        config_path = self._write_config_and_items(tmp_path, items_data=items)
        out_file = tmp_path / "output.jsonl"
        monkeypatch.setattr(
            "sys.argv",
            [
                "generate_units.py",
                "--config", str(config_path),
                "--output", str(out_file),
                "--max-units", "3",
            ],
        )
        main()
        captured = capsys.readouterr()
        # stderr has two lines: the "Applied max_units" message and the summary JSON
        stderr_lines = captured.err.strip().split("\n")
        summary = json.loads(stderr_lines[-1])
        assert summary["summary"]["max_units_applied"] == 3

    def test_invalid_yaml_config_exits(self, tmp_path, monkeypatch, capsys):
        """main() exits when config file contains invalid YAML."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("{{invalid yaml::")
        monkeypatch.setattr(
            "sys.argv",
            ["generate_units.py", "--config", str(config_path)],
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
