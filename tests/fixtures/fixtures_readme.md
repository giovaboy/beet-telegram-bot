# 📦 Test Fixtures

This directory contains real beet output samples used for testing.

## 🎯 Purpose

Fixtures allow us to test the parser without needing:
- A running beet instance
- Access to MusicBrainz/Discogs
- Actual music files

Instead, we capture real beet output once and replay it in tests.

## 📝 How to Add a Fixture

### Step 1: Capture Beet Output

Run your beet command and capture **everything** (stdout + stderr):

```bash
# For Docker setup:
docker exec -u abc beets beet import -vv /path/to/album > output.txt 2>&1

# Without Docker:
beet import -vv /path/to/album > output.txt 2>&1
```

**Important:** Use `-vv` for verbose output! This gives us the IDs we need.

### Step 2: Add Metadata Header

At the top of the file, add a comment block:

```
# Test Case: [Brief description]
# Album: [Album name]
# Artist: [Artist name]
# Source: [MusicBrainz | Discogs | Both]
# Expected Status: [single_match | has_candidates | no_match | success | error]
# Expected Similarity: [>90 | 80-90 | <80 | N/A]
# Notes: [Any special characteristics]
# ---
[actual beet output here]
```

### Step 3: Name the File

Use descriptive names following this pattern:

```
{scenario}_{source}_{case}.txt

Examples:
- single_match_mb_high_sim.txt
- single_match_discogs_low_sim.txt
- multiple_candidates_mb_mixed.txt
- no_match_unknown_album.txt
- error_timeout.txt
- success_clean.txt
```

### Step 4: Verify the Fixture

Run tests to ensure it parses correctly:

```bash
pytest tests/test_parsers.py -k "your_test_name" -v
```

## 📋 Required Fixtures

At minimum, we need these scenarios:

### Single Match
- ✅ `single_match_mb.txt` - MusicBrainz with high similarity (>90%)
- ✅ `single_match_mb_low_sim.txt` - MusicBrainz with low similarity (70-80%)
- ✅ `single_match_discogs.txt` - Discogs match
- ✅ `single_match_with_differences.txt` - Match with many differences

### Multiple Candidates
- ✅ `multiple_candidates_mb.txt` - MusicBrainz candidates
- ✅ `multiple_candidates_discogs.txt` - Discogs candidates
- ✅ `multiple_candidates_mixed.txt` - Both MB and Discogs

### No Match
- ✅ `no_match.txt` - No candidates found
- ✅ `low_similarity.txt` - Candidates too low similarity

### Success
- ✅ `success.txt` - Successfully imported
- ✅ `success_already_imported.txt` - Already in library

### Errors
- ✅ `error_timeout.txt` - Command timeout
- ✅ `error_no_files.txt` - No audio files found
- ✅ `error_permission_denied.txt` - Permission error

### Special Cases
- ✅ `multi_disc.txt` - Multi-disc album
- ✅ `unicode_artist.txt` - Non-ASCII characters (Björk, etc.)
- ✅ `compilation.txt` - Various Artists compilation

## 🔍 Example Fixture

Here's a complete example:

```
# Test Case: Single match with high similarity
# Album: Pink Floyd - The Wall
# Artist: Pink Floyd
# Source: MusicBrainz
# Expected Status: single_match
# Expected Similarity: >95
# Notes: Classic album, should always match well
# ---
Sending event: import_task_start
user configuration: /config/config.yaml
data directory: /config
library database: /config/musiclibrary.blb
library directory: /music
plugins: discogs fetchart lastgenre scrub

Tagging:
    Pink Floyd - The Wall
Import started: /downloads/Pink_Floyd-The_Wall

match: Candidate: Pink Floyd - The Wall (abc12345-1234-5678-9012-123456789abc)
Distance: 0.045

Looking up: /downloads/Pink_Floyd-The_Wall
/downloads/Pink_Floyd-The_Wall (26 items)

Match (95.5%):
  Pink Floyd - The Wall
* Artist: Pink Floyd
* Album: The Wall
* Year: 1979
MusicBrainz, 2 × Vinyl, LP, Album, 1979, US, Columbia, PC2 36183

≠ artist: Pink Floyd -> Pink Floyd, The
≠ (#1) In the Flesh? (3:20) -> (#1) In the Flesh? (3:19)
≠ (#13) Run Like Hell (4:19) -> (#13) Run Like Hell (4:20)

Apply this match? [Y/n/s/e/q]
```

## 🧪 Testing Your Fixture

After adding a fixture, create or update a test:

```python
def test_my_new_fixture(fixtures_dir):
    """Test parsing of my new fixture"""
    content, metadata = load_fixture(fixtures_dir, "my_fixture.txt")
    
    result = parse_beet_output(content, "", "/test/path")
    
    # Verify expected status
    assert result['status'] == metadata.get('expected status', 'single_match')
    
    # Add specific assertions based on metadata
    if metadata.get('expected similarity'):
        # Parse and check similarity
        pass
```

## 📊 Coverage Goals

We should have fixtures covering:

- ✅ All status types (single_match, has_candidates, no_match, success, error, needs_input)
- ✅ Both metadata sources (MusicBrainz and Discogs)
- ✅ Various similarity ranges (>90%, 80-90%, 70-80%, <70%)
- ✅ Different album types (single disc, multi-disc, compilation)
- ✅ Edge cases (unicode, very long names, special characters)
- ✅ Error conditions (timeout, no files, permission denied)

## 🚫 What NOT to Include

Don't commit fixtures with:

- ❌ Personal information (paths with your username)
- ❌ API tokens or credentials
- ❌ Extremely large outputs (>100KB)
- ❌ Duplicate scenarios (we only need one example of each case)

If your output has personal info, sanitize it:

```bash
sed -i 's/\/home\/yourname/\/home\/user/g' fixture.txt
sed -i 's/user_token: abc123/user_token: REDACTED/g' fixture.txt
```

## 🔄 Updating Fixtures

When beet output format changes:

1. Capture new output with current beet version
2. Keep old fixture as `[name]_legacy.txt`
3. Update tests to work with both formats
4. Document the beet version in metadata

Example:
```
# Beet Version: 1.6.0
# Last Updated: 2025-01-15
```

## 💡 Tips

- **Use real albums** - Real data reveals real bugs
- **Keep it diverse** - Different genres, years, regions
- **Document quirks** - Note any unusual parsing behaviors
- **Version control** - Commit fixtures with meaningful messages
- **Test first** - Add the test, then the fixture

## 🆘 Troubleshooting

### Fixture Not Loading

```python
pytest.skip(f"Fixture not found: {filename}")
```

Check:
1. File exists in `tests/fixtures/`
2. Filename matches exactly (case-sensitive)
3. File has read permissions

### Parse Failures

If a fixture parses incorrectly:

1. Check metadata format (must have `# ---` separator)
2. Ensure UTF-8 encoding
3. Look for ANSI codes (should be included, parser will clean)
4. Verify output is complete (not truncated)

### Can't Capture Output

If `beet` hangs waiting for input:

```bash
# Use echo to provide input
echo "s" | beet import /path/to/album > output.txt 2>&1

# Or use pretend mode
beet import --pretend /path/to/album > output.txt 2>&1
```

## 📚 Resources

- [Beets Documentation](https://beets.readthedocs.io/)
- [MusicBrainz ID Format](https://musicbrainz.org/doc/MusicBrainz_Identifier)
- [Discogs ID Format](https://www.discogs.com/developers)

---

**Remember:** Good fixtures make good tests. Take the time to capture diverse, real-world scenarios!
