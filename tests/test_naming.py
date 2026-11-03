from app.naming import natural_key, nice_duration, story_title, strip_number_prefix, title_from_filename


def test_natural_order_puts_part_2_before_part_10():
    names = ["Part 10", "Part 2", "part 1", "Part 11"]
    assert sorted(names, key=natural_key) == ["part 1", "Part 2", "Part 10", "Part 11"]


def test_natural_key_handles_leading_digits_and_mixed_names():
    names = ["10 b", "2 a", "a", "2 b"]
    assert sorted(names, key=natural_key) == ["2 a", "2 b", "10 b", "a"]


def test_number_prefix_is_stripped_only_when_it_is_an_ordering_prefix():
    assert strip_number_prefix("01 - The Farm") == "The Farm"
    assert strip_number_prefix("2. Harvest") == "Harvest"
    assert strip_number_prefix("03_Storm") == "Storm"
    assert strip_number_prefix("1984 - Orwell") == "1984 - Orwell"  # four digits: part of the name
    assert strip_number_prefix("3.5 mm film") == "3.5 mm film"
    assert strip_number_prefix("12 Angry Men") == "12 Angry Men"
    assert strip_number_prefix("01 -") == "01 -"  # nothing left, keep original


def test_title_from_filename():
    assert title_from_filename("03_the_last_day") == "the last day"
    assert title_from_filename("Apple  Pie") == "Apple Pie"
    assert title_from_filename("My_Mixed name_ok") == "My_Mixed name_ok"


def test_story_title():
    assert story_title("") == "Other Videos"
    assert story_title("05 - Gamma Story") == "Gamma Story"


def test_nice_duration():
    assert nice_duration(None) == ""
    assert nice_duration(20) == "1 minute"
    assert nice_duration(12 * 60) == "12 minutes"
    assert nice_duration(65 * 60) == "1 hour 5 minutes"
    assert nice_duration(120 * 60) == "2 hours"
