from ctypes import pythonapi

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash
)

import sqlite3
from functools import wraps


# =========================
# FLASK APP
# =========================

app = Flask(__name__)

app.secret_key = "quiz_software_secret_key"


# =========================
# DATABASE
# =========================

DATABASE = "quiz.db"


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# =========================
# INITIALIZE DATABASE
# =========================

def init_db():

    conn = get_db()
    cursor = conn.cursor()

    # USERS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            full_name TEXT NOT NULL
        )
    """)

    # QUIZZES
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            teacher_id INTEGER,
            FOREIGN KEY (teacher_id) REFERENCES users(id)
        )
    """)

    # QUESTIONS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
        )
    """)

    # RESULTS
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            quiz_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            FOREIGN KEY (student_id) REFERENCES users(id),
            FOREIGN KEY (quiz_id) REFERENCES quizzes(id)
        )
    """)

    # MESSAGES
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sender_id) REFERENCES users(id),
            FOREIGN KEY (receiver_id) REFERENCES users(id)
        )
    """)

    # CREATE DEFAULT TEACHER
    teacher = cursor.execute("""
        SELECT id
        FROM users
        WHERE username = ?
    """, ("teacher",)).fetchone()

    if not teacher:

        cursor.execute("""
            INSERT INTO users
            (username, password, role, full_name)
            VALUES (?, ?, ?, ?)
        """, (
            "teacher",
            "teacher123",
            "teacher",
            "Main Teacher"
        ))

    conn.commit()
    conn.close()


# =========================
# LOGIN REQUIRED
# =========================

def login_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):

        if "user_id" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))

        return f(*args, **kwargs)

    return decorated_function


# =========================
# TEACHER REQUIRED
# =========================

def teacher_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):

        if "user_id" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))

        if session.get("role") != "teacher":
            flash("Teacher access required.")
            return redirect(url_for("student_dashboard"))

        return f(*args, **kwargs)

    return decorated_function


# =========================
# STUDENT REQUIRED
# =========================

def student_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):

        if "user_id" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))

        if session.get("role") != "student":
            flash("Student access required.")
            return redirect(url_for("teacher_dashboard"))

        return f(*args, **kwargs)

    return decorated_function


# =========================
# HOME
# =========================

@app.route("/")
def index():

    if "user_id" in session:

        if session.get("role") == "teacher":
            return redirect(url_for("teacher_dashboard"))

        return redirect(url_for("student_dashboard"))

    return render_template("index.html")


# =========================
# REGISTER
# =========================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        username = request.form["username"].strip()
        password = request.form["password"].strip()
        full_name = request.form["full_name"].strip()

        if not username or not password or not full_name:

            flash("All fields are required.")
            return redirect(url_for("register"))

        conn = get_db()

        existing_user = conn.execute("""
            SELECT id
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()

        if existing_user:

            conn.close()

            flash("Username already exists.")
            return redirect(url_for("register"))

        conn.execute("""
            INSERT INTO users
            (username, password, role, full_name)
            VALUES (?, ?, ?, ?)
        """, (
            username,
            password,
            "student",
            full_name
        ))

        conn.commit()
        conn.close()

        flash("Registration successful. You can now log in.")

        return redirect(url_for("login"))

    return render_template("register.html")


# =========================
# LOGIN
# =========================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"].strip()
        password = request.form["password"].strip()

        conn = get_db()

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE username = ?
            AND password = ?
        """, (
            username,
            password
        )).fetchone()

        conn.close()

        if not user:

            flash("Invalid username or password.")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        session["full_name"] = user["full_name"]

        if user["role"] == "teacher":

            return redirect(url_for("teacher_dashboard"))

        return redirect(url_for("student_dashboard"))

    return render_template("login.html")


# =========================
# LOGOUT
# =========================

@app.route("/logout")
def logout():

    session.clear()

    flash("You have been logged out.")

    return redirect(url_for("login"))


# =========================
# STUDENT DASHBOARD
# =========================

@app.route("/student")
@student_required
def student_dashboard():

    conn = get_db()

    quizzes = conn.execute("""
        SELECT
            quizzes.id,
            quizzes.title,
            quizzes.description,
            users.full_name AS teacher
        FROM quizzes
        LEFT JOIN users
        ON quizzes.teacher_id = users.id
        ORDER BY quizzes.id DESC
    """).fetchall()

    results = conn.execute("""
        SELECT
            results.score,
            results.total,
            quizzes.title
        FROM results
        JOIN quizzes
        ON results.quiz_id = quizzes.id
        WHERE results.student_id = ?
        ORDER BY results.id DESC
    """, (
        session["user_id"],
    )).fetchall()

    conn.close()

    return render_template(
        "student_dashboard.html",
        quizzes=quizzes,
        results=results
    )


# =========================
# STUDENT MESSAGES
# =========================

@app.route("/student/messages")
@student_required
def student_messages():

    conn = get_db()

    messages = conn.execute("""
        SELECT
            messages.message,
            messages.created_at,
            users.full_name AS sender
        FROM messages
        JOIN users
        ON messages.sender_id = users.id
        WHERE messages.receiver_id = ?
        ORDER BY messages.id DESC
    """, (
        session["user_id"],
    )).fetchall()

    conn.close()

    return render_template(
        "student_messages.html",
        messages=messages
    )


# =========================
# TEACHER DASHBOARD
# =========================

@app.route("/teacher")
@teacher_required
def teacher_dashboard():

    conn = get_db()

    quizzes = conn.execute("""
        SELECT
            quizzes.id,
            quizzes.title,
            quizzes.description,
            COUNT(questions.id) AS question_count
        FROM quizzes
        LEFT JOIN questions
        ON quizzes.id = questions.quiz_id
        WHERE quizzes.teacher_id = ?
        GROUP BY quizzes.id
        ORDER BY quizzes.id DESC
    """, (
        session["user_id"],
    )).fetchall()

    students = conn.execute("""
        SELECT COUNT(*) AS count
        FROM users
        WHERE role = 'student'
    """).fetchone()["count"]

    conn.close()

    return render_template(
        "teacher_dashboard.html",
        quizzes=quizzes,
        students=students
    )


# =========================
# CREATE QUIZ
# =========================

@app.route("/teacher/create_quiz", methods=["GET", "POST"])
@teacher_required
def create_quiz():

    if request.method == "POST":

        title = request.form["title"].strip()
        description = request.form["description"].strip()

        if not title:

            flash("Quiz title is required.")

            return redirect(url_for("create_quiz"))

        conn = get_db()

        cursor = conn.execute("""
            INSERT INTO quizzes
            (title, description, teacher_id)
            VALUES (?, ?, ?)
        """, (
            title,
            description,
            session["user_id"]
        ))

        quiz_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return redirect(
            url_for("add_question", quiz_id=quiz_id)
        )

    return render_template("create_quiz.html")


# =========================
# EDIT QUIZ
# =========================

@app.route(
    "/teacher/quiz/<int:quiz_id>/edit",
    methods=["GET", "POST"]
)
@teacher_required
def edit_quiz(quiz_id):

    conn = get_db()

    quiz = conn.execute("""
        SELECT *
        FROM quizzes
        WHERE id = ?
        AND teacher_id = ?
    """, (
        quiz_id,
        session["user_id"]
    )).fetchone()

    if not quiz:

        conn.close()

        flash("Quiz not found.")

        return redirect(url_for("teacher_dashboard"))

    if request.method == "POST":

        title = request.form["title"].strip()
        description = request.form["description"].strip()

        if not title:

            flash("Quiz title is required.")

            conn.close()

            return redirect(
                url_for(
                    "edit_quiz",
                    quiz_id=quiz_id
                )
            )

        conn.execute("""
            UPDATE quizzes
            SET title = ?,
                description = ?
            WHERE id = ?
            AND teacher_id = ?
        """, (
            title,
            description,
            quiz_id,
            session["user_id"]
        ))

        conn.commit()
        conn.close()

        flash("Quiz updated successfully.")

        return redirect(
            url_for("teacher_dashboard")
        )

    conn.close()

    return render_template(
        "edit_quiz.html",
        quiz=quiz
    )


# =========================
# ADD QUESTION
# =========================

@app.route(
    "/teacher/quiz/<int:quiz_id>/add_question",
    methods=["GET", "POST"]
)
@teacher_required
def add_question(quiz_id):

    conn = get_db()

    quiz = conn.execute("""
        SELECT *
        FROM quizzes
        WHERE id = ?
        AND teacher_id = ?
    """, (
        quiz_id,
        session["user_id"]
    )).fetchone()

    if not quiz:

        conn.close()

        flash("Quiz not found.")

        return redirect(
            url_for("teacher_dashboard")
        )

    if request.method == "POST":

        question = request.form["question"].strip()
        option_a = request.form["option_a"].strip()
        option_b = request.form["option_b"].strip()
        option_c = request.form["option_c"].strip()
        option_d = request.form["option_d"].strip()
        correct_answer = request.form["correct_answer"].strip()

        if not question:
            flash("Question is required.")
            conn.close()
            return redirect(
                url_for(
                    "add_question",
                    quiz_id=quiz_id
                )
            )

        conn.execute("""
            INSERT INTO questions
            (
                quiz_id,
                question,
                option_a,
                option_b,
                option_c,
                option_d,
                correct_answer
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            quiz_id,
            question,
            option_a,
            option_b,
            option_c,
            option_d,
            correct_answer
        ))

        conn.commit()

        flash("Question added successfully.")

        return redirect(
            url_for(
                "add_question",
                quiz_id=quiz_id
            )
        )

    questions = conn.execute("""
        SELECT *
        FROM questions
        WHERE quiz_id = ?
        ORDER BY id
    """, (
        quiz_id,
    )).fetchall()

    conn.close()

    return render_template(
        "add_question.html",
        quiz=quiz,
        questions=questions
    )


# =========================
# TAKE QUIZ
# =========================

@app.route(
    "/student/quiz/<int:quiz_id>",
    methods=["GET", "POST"]
)
@student_required
def take_quiz(quiz_id):

    conn = get_db()

    quiz = conn.execute("""
        SELECT *
        FROM quizzes
        WHERE id = ?
    """, (
        quiz_id,
    )).fetchone()

    if not quiz:

        conn.close()

        flash("Quiz not found.")

        return redirect(
            url_for("student_dashboard")
        )

    questions = conn.execute("""
        SELECT *
        FROM questions
        WHERE quiz_id = ?
        ORDER BY id
    """, (
        quiz_id,
    )).fetchall()

    if request.method == "POST":

        score = 0
        total = len(questions)

        for question in questions:

            answer = request.form.get(
                f"question_{question['id']}"
            )

            if answer == question["correct_answer"]:
                score += 1

        conn.execute("""
            INSERT INTO results
            (
                student_id,
                quiz_id,
                score,
                total
            )
            VALUES (?, ?, ?, ?)
        """, (
            session["user_id"],
            quiz_id,
            score,
            total
        ))

        conn.commit()
        conn.close()

        return render_template(
            "result.html",
            quiz=quiz,
            score=score,
            total=total
        )

    conn.close()

    return render_template(
        "take_quiz.html",
        quiz=quiz,
        questions=questions
    )


# =========================
# TEACHER RESULTS
# =========================

@app.route("/teacher/results")
@teacher_required
def teacher_results():

    conn = get_db()

    results = conn.execute("""
        SELECT
            users.full_name,
            users.username,
            quizzes.title,
            results.score,
            results.total
        FROM results
        JOIN users
        ON results.student_id = users.id
        JOIN quizzes
        ON results.quiz_id = quizzes.id
        WHERE quizzes.teacher_id = ?
        ORDER BY results.id DESC
    """, (
        session["user_id"],
    )).fetchall()

    conn.close()

    return render_template(
        "teacher_results.html",
        results=results
    )


# =========================
# TEACHER MESSAGES
# =========================

@app.route(
    "/teacher/messages",
    methods=["GET", "POST"]
)
@teacher_required
def teacher_messages():

    conn = get_db()

    if request.method == "POST":

        receiver_id = request.form["receiver_id"]
        message = request.form["message"].strip()

        if message:

            conn.execute("""
                INSERT INTO messages
                (
                    sender_id,
                    receiver_id,
                    message
                )
                VALUES (?, ?, ?)
            """, (
                session["user_id"],
                receiver_id,
                message
            ))

            conn.commit()

            flash("Message sent successfully.")

    students = conn.execute("""
        SELECT
            id,
            username,
            full_name
        FROM users
        WHERE role = 'student'
        ORDER BY full_name
    """).fetchall()

    conn.close()

    return render_template(
        "teacher_messages.html",
        students=students
    )


# =========================
# RUN APPLICATION
# =========================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True
    )
    
