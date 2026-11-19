"""Assignment 3 tests (Gavin, Member A).

Notification success path for the hold-available event (stub returning True,
mocks checking recipient, subject and message), plus coverage-directed tests
for the loans web pages in app/routes/views.py.
"""
from datetime import date, timedelta
from unittest.mock import patch

import notification_service
from app import store

SUBJECT = "Your hold is ready for pickup"


# ---------- helpers ----------

def make_book(client, title="Dune", copies=1):
    return client.post("/books", json={
        "title": title, "author": "Frank Herbert",
        "isbn": "978-0441013593", "total_copies": copies,
    }).get_json()


def make_member(client, name="Alice", email="alice@example.com"):
    return client.post("/members", json={"name": name, "email": email}).get_json()


def lent_book_with_hold(client):
    """Alice borrows the only copy of Dune and Bob places a hold on it."""
    book = make_book(client)
    alice = make_member(client)
    bob = make_member(client, "Bob", "bob@example.com")
    loan = client.post("/loans", json={"book_id": book["id"], "member_id": alice["id"]}).get_json()
    hold = client.post("/reservations", json={"book_id": book["id"], "member_id": bob["id"]}).get_json()
    return book, bob, loan, hold


# ---------- notification: success path ----------

def test_return_reports_hold_notification_sent_when_service_succeeds(client, monkeypatch):
    monkeypatch.setattr("app.routes.loans.send_notification",
                        lambda recipient, subject, message: True)   # stub
    book, bob, loan, hold = lent_book_with_hold(client)

    response = client.put(f"/loans/{loan['id']}/return")

    assert response.status_code == 200
    body = response.get_json()
    assert body["return_date"] == date.today().isoformat()
    assert body["hold_notification"] == {
        "reservation_id": hold["id"], "member_id": bob["id"], "sent": True}
    stored = client.get(f"/reservations?member_id={bob['id']}").get_json()[0]
    assert stored["notified"] is True
    assert stored["fulfilled"] is False   # notified, not yet checked out
    assert client.get(f"/books/{book['id']}").get_json()["available_copies"] == 1


def test_hold_notification_uses_member_email_subject_and_message(client):
    book, bob, loan, hold = lent_book_with_hold(client)

    with patch("app.routes.loans.send_notification", return_value=True) as mock_send:
        response = client.put(f"/loans/{loan['id']}/return")

    mock_send.assert_called_once_with(
        "bob@example.com", SUBJECT,
        "Hi Bob, a copy of 'Dune' has been returned and is being held for you.")
    assert response.status_code == 200
    assert response.get_json()["hold_notification"]["sent"] is True


def test_only_first_member_in_hold_queue_is_notified(client):
    book, bob, loan, first_hold = lent_book_with_hold(client)
    carol = make_member(client, "Carol", "carol@example.com")
    client.post("/reservations", json={"book_id": book["id"], "member_id": carol["id"]})

    with patch("app.routes.loans.send_notification", return_value=True) as mock_send:
        response = client.put(f"/loans/{loan['id']}/return")

    assert mock_send.call_count == 1
    assert mock_send.call_args.args[0] == "bob@example.com"
    assert response.get_json()["hold_notification"]["member_id"] == bob["id"]
    carol_hold = client.get(f"/reservations?member_id={carol['id']}").get_json()[0]
    assert carol_hold["notified"] is False


def test_web_return_notifies_next_member_on_hold(client):
    book, bob, loan, hold = lent_book_with_hold(client)

    with patch("app.routes.loans.send_notification", return_value=True) as mock_send:
        response = client.post(f"/ui/loans/{loan['id']}/return")

    assert response.status_code == 302
    mock_send.assert_called_once_with(
        "bob@example.com", SUBJECT,
        "Hi Bob, a copy of 'Dune' has been returned and is being held for you.")
    assert store.reservations[hold["id"]].notified is True
    assert store.loans[loan["id"]].return_date == date.today()


def test_simulator_records_hold_notification_with_library_prefix(client):
    # No test double: the real (customized) simulator records the message.
    notification_service.clear_notifications()
    book, bob, loan, hold = lent_book_with_hold(client)

    response = client.put(f"/loans/{loan['id']}/return")

    assert response.get_json()["hold_notification"]["sent"] is True
    assert notification_service.get_notifications() == [{
        "recipient": "bob@example.com",
        "subject": "[City Library] " + SUBJECT,
        "message": "Hi Bob, a copy of 'Dune' has been returned and is being held for you.",
    }]
    notification_service.clear_notifications()


# ---------- coverage: loans web pages (views.py) ----------

def test_home_page_links_to_all_sections(client):
    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for path in ("/ui/books", "/ui/members", "/ui/loans"):
        assert path in html


def test_loans_page_lists_existing_loans(client):
    book = make_book(client)
    alice = make_member(client)
    loan = client.post("/loans", json={"book_id": book["id"], "member_id": alice["id"]}).get_json()

    response = client.get("/ui/loans")

    assert response.status_code == 200
    assert loan["due_date"] in response.get_data(as_text=True)


def test_web_checkout_creates_loan_and_takes_a_copy(client):
    book = make_book(client, copies=2)
    alice = make_member(client)

    response = client.post("/ui/loans", data={"book_id": book["id"], "member_id": alice["id"]})

    assert response.status_code == 302
    loans = client.get("/loans").get_json()
    assert len(loans) == 1 and loans[0]["member_id"] == alice["id"]
    assert loans[0]["due_date"] == (date.today() + timedelta(days=14)).isoformat()
    assert client.get(f"/books/{book['id']}").get_json()["available_copies"] == 1


def test_web_checkout_refused_while_member_has_outstanding_fine(client):
    book = make_book(client, copies=2)
    alice = make_member(client)
    loan = client.post("/loans", json={"book_id": book["id"], "member_id": alice["id"]}).get_json()
    store.loans[loan["id"]].due_date = date.today() - timedelta(days=2)   # overdue

    response = client.post("/ui/loans", data={"book_id": book["id"], "member_id": alice["id"]})

    assert response.status_code == 409
    assert "Outstanding fines" in response.get_data(as_text=True)
    assert len(client.get("/loans").get_json()) == 1
    assert client.get(f"/books/{book['id']}").get_json()["available_copies"] == 1


def test_web_checkout_refused_when_copy_is_held_for_someone_else(client):
    book = make_book(client, copies=1)
    bob = make_member(client, "Bob", "bob@example.com")
    carol = make_member(client, "Carol", "carol@example.com")
    client.post("/reservations", json={"book_id": book["id"], "member_id": bob["id"]})

    response = client.post("/ui/loans", data={"book_id": book["id"], "member_id": carol["id"]})

    assert response.status_code == 409
    assert response.get_data(as_text=True) == "Book is on hold for another member"
    assert client.get("/loans").get_json() == []


def test_web_checkout_of_unknown_book_creates_no_loan(client):
    alice = make_member(client)

    response = client.post("/ui/loans", data={"book_id": 999, "member_id": alice["id"]})

    assert response.status_code == 302
    assert client.get("/loans").get_json() == []


def test_web_return_sets_return_date_and_restores_copy_once(client):
    book = make_book(client, copies=1)
    alice = make_member(client)
    loan = client.post("/loans", json={"book_id": book["id"], "member_id": alice["id"]}).get_json()

    first = client.post(f"/ui/loans/{loan['id']}/return")
    second = client.post(f"/ui/loans/{loan['id']}/return")   # already returned

    assert first.status_code == 302 and second.status_code == 302
    assert store.loans[loan["id"]].return_date == date.today()
    assert client.get(f"/books/{book['id']}").get_json()["available_copies"] == 1
