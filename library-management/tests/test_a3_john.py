"""Assignment 3 tests (John, Member B).

Notification failure and non-trigger paths for the hold-available event
(stubs returning False, mocks asserting no call is made), plus coverage-directed
tests for the books and members web pages and the 404 paths of the book and
member APIs.
"""
from datetime import date
from unittest.mock import patch

from app import store


# ---------- helpers ----------

def make_book(client, title="Dune", copies=1):
    return client.post("/books", json={
        "title": title, "author": "Frank Herbert",
        "isbn": "978-0441013593", "total_copies": copies,
    }).get_json()


def make_member(client, name="Alice", email="alice@example.com"):
    return client.post("/members", json={"name": name, "email": email}).get_json()


def checkout(client, book_id, member_id):
    return client.post("/loans", json={"book_id": book_id, "member_id": member_id}).get_json()


# ---------- coverage: books and members web pages (views.py) ----------

def test_books_page_lists_books(client):
    make_book(client, "Emma")

    response = client.get("/ui/books")

    assert response.status_code == 200
    assert "Emma" in response.get_data(as_text=True)


def test_books_page_form_adds_book_with_all_copies_available(client):
    response = client.post("/ui/books", data={
        "title": "Ulysses", "author": "James Joyce", "isbn": "123", "total_copies": "3"})

    assert response.status_code == 302
    books = client.get("/books").get_json()
    assert len(books) == 1
    assert books[0]["title"] == "Ulysses"
    assert books[0]["total_copies"] == 3 and books[0]["available_copies"] == 3


def test_books_page_delete_removes_book_and_ignores_unknown_id(client):
    book = make_book(client)

    removed = client.post(f"/ui/books/{book['id']}/delete")
    unknown = client.post("/ui/books/999/delete")

    assert removed.status_code == 302 and unknown.status_code == 302
    assert client.get(f"/books/{book['id']}").status_code == 404


def test_members_page_lists_members(client):
    make_member(client, "Zoe", "zoe@example.com")

    response = client.get("/ui/members")

    assert response.status_code == 200
    assert "zoe@example.com" in response.get_data(as_text=True)


def test_members_page_form_registers_member(client):
    response = client.post("/ui/members", data={"name": "Ian", "email": "ian@example.com"})

    assert response.status_code == 302
    members = client.get("/members").get_json()
    assert [(m["name"], m["email"]) for m in members] == [("Ian", "ian@example.com")]
    assert members[0]["membership_date"] == date.today().isoformat()


def test_members_page_delete_removes_member(client):
    member = make_member(client)

    response = client.post(f"/ui/members/{member['id']}/delete")

    assert response.status_code == 302
    assert client.get(f"/members/{member['id']}").status_code == 404


# ---------- coverage: 404 paths of the book and member APIs ----------

def test_update_unknown_book_returns_404_and_creates_nothing(client):
    response = client.put("/books/999", json={"title": "Ghost"})

    assert response.status_code == 404
    assert response.get_json() == {"error": "Book not found"}
    assert client.get("/books").get_json() == []


def test_delete_unknown_book_returns_404(client):
    make_book(client)

    response = client.delete("/books/999")

    assert response.status_code == 404
    assert len(client.get("/books").get_json()) == 1   # existing book untouched


def test_update_unknown_member_returns_404(client):
    response = client.put("/members/999", json={"name": "Ghost"})

    assert response.status_code == 404
    assert response.get_json() == {"error": "Member not found"}
    assert client.get("/members").get_json() == []


def test_delete_unknown_member_returns_404(client):
    make_member(client)

    response = client.delete("/members/999")

    assert response.status_code == 404
    assert len(client.get("/members").get_json()) == 1


# ---------- notification: failure path ----------

def test_failed_hold_notification_does_not_block_the_return(client, monkeypatch):
    monkeypatch.setattr("app.routes.loans.send_notification",
                        lambda recipient, subject, message: False)   # stub
    book = make_book(client)
    alice = make_member(client)
    bob = make_member(client, "Bob", "bob@example.com")
    loan = checkout(client, book["id"], alice["id"])
    hold = client.post("/reservations", json={"book_id": book["id"], "member_id": bob["id"]}).get_json()

    response = client.put(f"/loans/{loan['id']}/return")

    assert response.status_code == 200
    body = response.get_json()
    assert body["return_date"] == date.today().isoformat()
    assert body["hold_notification"] == {
        "reservation_id": hold["id"], "member_id": bob["id"], "sent": False}
    assert store.reservations[hold["id"]].notified is False
    assert client.get(f"/books/{book['id']}").get_json()["available_copies"] == 1


def test_failed_notification_is_retried_when_next_copy_is_returned(client, monkeypatch):
    book = make_book(client, copies=2)
    alice = make_member(client)
    dave = make_member(client, "Dave", "dave@example.com")
    bob = make_member(client, "Bob", "bob@example.com")
    first = checkout(client, book["id"], alice["id"])
    second = checkout(client, book["id"], dave["id"])
    hold = client.post("/reservations", json={"book_id": book["id"], "member_id": bob["id"]}).get_json()

    monkeypatch.setattr("app.routes.loans.send_notification", lambda r, s, m: False)  # stub: fails
    failed = client.put(f"/loans/{first['id']}/return").get_json()
    monkeypatch.setattr("app.routes.loans.send_notification", lambda r, s, m: True)   # stub: works
    retried = client.put(f"/loans/{second['id']}/return").get_json()

    assert failed["hold_notification"]["sent"] is False
    assert retried["hold_notification"] == {
        "reservation_id": hold["id"], "member_id": bob["id"], "sent": True}
    assert store.reservations[hold["id"]].notified is True


def test_web_return_still_succeeds_when_notification_fails(client, monkeypatch):
    monkeypatch.setattr("app.routes.loans.send_notification", lambda r, s, m: False)  # stub
    book = make_book(client)
    alice = make_member(client)
    bob = make_member(client, "Bob", "bob@example.com")
    loan = checkout(client, book["id"], alice["id"])
    hold = client.post("/reservations", json={"book_id": book["id"], "member_id": bob["id"]}).get_json()

    response = client.post(f"/ui/loans/{loan['id']}/return")

    assert response.status_code == 302
    assert store.loans[loan["id"]].return_date == date.today()
    assert store.books[book["id"]].available_copies == 1
    assert store.reservations[hold["id"]].notified is False


# ---------- notification: non-trigger paths ----------

def test_return_without_any_hold_sends_no_notification(client):
    book = make_book(client)
    alice = make_member(client)
    loan = checkout(client, book["id"], alice["id"])

    with patch("app.routes.loans.send_notification") as mock_send:
        response = client.put(f"/loans/{loan['id']}/return")

    mock_send.assert_not_called()
    assert response.status_code == 200
    assert response.get_json()["hold_notification"] is None


def test_member_already_notified_is_not_notified_again(client):
    book = make_book(client, copies=2)
    alice = make_member(client)
    dave = make_member(client, "Dave", "dave@example.com")
    bob = make_member(client, "Bob", "bob@example.com")
    first = checkout(client, book["id"], alice["id"])
    second = checkout(client, book["id"], dave["id"])
    client.post("/reservations", json={"book_id": book["id"], "member_id": bob["id"]})
    with patch("app.routes.loans.send_notification", return_value=True):
        client.put(f"/loans/{first['id']}/return")       # Bob notified here

    with patch("app.routes.loans.send_notification") as mock_send:
        response = client.put(f"/loans/{second['id']}/return")

    mock_send.assert_not_called()
    assert response.get_json()["hold_notification"] is None
    assert client.get(f"/books/{book['id']}").get_json()["available_copies"] == 2


def test_fulfilled_hold_does_not_trigger_notification(client):
    book = make_book(client)
    bob = make_member(client, "Bob", "bob@example.com")
    client.post("/reservations", json={"book_id": book["id"], "member_id": bob["id"]})
    loan = checkout(client, book["id"], bob["id"])        # Bob collects his hold

    with patch("app.routes.loans.send_notification") as mock_send:
        response = client.put(f"/loans/{loan['id']}/return")

    mock_send.assert_not_called()
    assert response.get_json()["hold_notification"] is None
    assert client.get(f"/reservations?member_id={bob['id']}").get_json()[0]["fulfilled"] is True


def test_hold_of_deleted_member_is_skipped(client):
    book = make_book(client)
    alice = make_member(client)
    bob = make_member(client, "Bob", "bob@example.com")
    loan = checkout(client, book["id"], alice["id"])
    client.post("/reservations", json={"book_id": book["id"], "member_id": bob["id"]})
    client.delete(f"/members/{bob['id']}")

    with patch("app.routes.loans.send_notification") as mock_send:
        response = client.put(f"/loans/{loan['id']}/return")

    mock_send.assert_not_called()
    assert response.status_code == 200
    assert response.get_json()["hold_notification"] is None
