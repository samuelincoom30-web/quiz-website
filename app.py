import os
import re
import uuid
from io import BytesIO
from datetime import datetime

import psycopg
import fitz

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file,
)

from psycopg.rows import dict_row


# ============================================================
# APP CONFIGURATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "dev-secret-key-change-this"
)

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Please check your environment variables."
    )


UPLOAD_FOLDER = "uploads"

os.makedirs(
    UPLOAD_FOLDER,
    exist_ok=True
)


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_db():

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10
    )


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():

    conn = get_db()

    try:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (

                id SERIAL PRIMARY KEY,

                username VARCHAR(100) UNIQUE NOT NULL,

                password VARCHAR(255) NOT NULL,

                role VARCHAR(20) NOT NULL,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS quizzes (

                id SERIAL PRIMARY KEY,

                teacher_id INTEGER
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                title VARCHAR(255) NOT NULL,

                description TEXT,

                published BOOLEAN DEFAULT FALSE,

                duration_minutes INTEGER DEFAULT 30,

                max_attempts INTEGER DEFAULT 1,

                randomize_questions BOOLEAN DEFAULT FALSE,

                randomize_options BOOLEAN DEFAULT FALSE,

                show_result_immediately BOOLEAN DEFAULT TRUE,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS questions (

                id SERIAL PRIMARY KEY,

                quiz_id INTEGER
                    REFERENCES quizzes(id)
                    ON DELETE CASCADE,

                question TEXT NOT NULL,

                option_a TEXT NOT NULL,

                option_b TEXT NOT NULL,

                option_c TEXT NOT NULL,

                option_d TEXT NOT NULL,

                correct_answer VARCHAR(1) NOT NULL

            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS attempts (

                id SERIAL PRIMARY KEY,

                quiz_id INTEGER
                    REFERENCES quizzes(id)
                    ON DELETE CASCADE,

                student_id INTEGER
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                score INTEGER DEFAULT 0,

                total INTEGER DEFAULT 0,

                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

            )
        """)


        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (

                id SERIAL PRIMARY KEY,

                sender_id INTEGER
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                receiver_id INTEGER
                    REFERENCES users(id)
                    ON DELETE CASCADE,

                subject VARCHAR(255),

                message TEXT,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                is_read BOOLEAN DEFAULT FALSE

            )
        """)
    
        conn.execute("""
        CREATE TABLE IF NOT EXISTS attempt_answers (

            id SERIAL PRIMARY KEY,

            attempt_id INTEGER
                REFERENCES attempts(id)
                ON DELETE CASCADE,

            question_id INTEGER
                REFERENCES questions(id)
                ON DELETE SET NULL,

            question_text TEXT NOT NULL,

            selected_answer TEXT,

            correct_answer TEXT NOT NULL,

            is_correct BOOLEAN DEFAULT FALSE

        )
    """)



        # ----------------------------------------------------
        # Make sure older databases also have these columns
        # ----------------------------------------------------

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS duration_minutes
            INTEGER DEFAULT 30
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS max_attempts
            INTEGER DEFAULT 1
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS randomize_questions
            BOOLEAN DEFAULT FALSE
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS randomize_options
            BOOLEAN DEFAULT FALSE
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS show_result_immediately
            BOOLEAN DEFAULT TRUE
        """)

        conn.execute("""
            ALTER TABLE quizzes
            ADD COLUMN IF NOT EXISTS published
            BOOLEAN DEFAULT FALSE
        """)


        conn.commit()


    finally:

        conn.close()


# ============================================================
# LOGIN PROTECTION
# ============================================================

def teacher_required(func):

    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return redirect(
                url_for("login")
            )

        if session.get("role") != "teacher":

            flash(
                "Teacher access required."
            )

            return redirect(
                url_for("login")
            )

        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__

    return wrapper


def student_required(func):

    def wrapper(*args, **kwargs):

        if "user_id" not in session:

            return redirect(
                url_for("login")
            )

        if session.get("role") != "student":

            flash(
                "Student access required."
            )

            return redirect(
                url_for("login")
            )

        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__

    return wrapper


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    if session.get("role") == "teacher":

        return redirect(
            url_for("teacher_dashboard")
        )

    if session.get("role") == "student":

        return redirect(
            url_for("student_dashboard")
        )

    return render_template(
        "index.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        try:

            user = conn.execute("""
                SELECT
                    id,
                    username,
                    password,
                    role
                FROM users
                WHERE username = %s
            """, (
                username,
            )).fetchone()

        finally:

            conn.close()


        if not user:

            flash(
                "Invalid username or password."
            )

            return redirect(
                url_for("login")
            )


        if user["password"] != password:

            flash(
                "Invalid username or password."
            )

            return redirect(
                url_for("login")
            )


        session["user_id"] = user["id"]

        session["username"] = user["username"]

        session["role"] = user["role"]


        if user["role"] == "teacher":

            return redirect(
                url_for("teacher_dashboard")
            )

        return redirect(
            url_for("student_dashboard")
        )


    return render_template(
        "login.html"
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# STUDENT REGISTRATION
# ============================================================
@app.route(
"/register",
methods=["GET", "POST"]
)
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not full_name or not username or not password:
            flash("Full name, username and password are required.")
            return redirect(url_for("register"))

        conn = get_db()

        try:
            existing = conn.execute("""
                SELECT id
                FROM users
                WHERE username = %s
            """, (username,)).fetchone()

            if existing:
                flash("Username already exists.")
                return redirect(url_for("register"))

            conn.execute("""
                INSERT INTO users
                    (full_name, username, password, role)
                VALUES (%s, %s, %s, 'student')
            """, (full_name, username, password))

            conn.commit()

        finally:
            conn.close()

        flash("Registration successful. You can now log in.")
        return redirect(url_for("login"))

    return render_template("register.html")
    

# ============================================================
# TEACHER DASHBOARD
# ============================================================

@app.route("/teacher/dashboard")
@teacher_required
def teacher_dashboard():

    conn = get_db()

    try:

        quizzes = conn.execute("""
            SELECT
                quizzes.id,
                quizzes.title,
                quizzes.description,
                quizzes.published,
                quizzes.duration_minutes,
                quizzes.max_attempts,
                COUNT(questions.id) AS question_count
            FROM quizzes

            LEFT JOIN questions
                ON quizzes.id = questions.quiz_id

            WHERE quizzes.teacher_id = %s

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


    finally:

        conn.close()


    return render_template(
        "teacher_dashboard.html",
        quizzes=quizzes,

        # Keep both names so your existing template works.
        students=students,
        student_count=students
    )


# ============================================================
# CREATE QUIZ
# ============================================================

@app.route(
    "/teacher/create-quiz",
    methods=["GET", "POST"]
)
@teacher_required
def create_quiz():

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        description = request.form.get(
            "description",
            ""
        ).strip()


        duration_minutes = request.form.get(
            "duration_minutes",
            30,
            type=int
        )

        max_attempts = request.form.get(
            "max_attempts",
            1,
            type=int
        )


        randomize_questions = (
            request.form.get(
                "randomize_questions"
            ) == "on"
        )


        randomize_options = (
            request.form.get(
                "randomize_options"
            ) == "on"
        )


        show_result_immediately = (
            request.form.get(
                "show_result_immediately"
            ) == "on"
        )


        if not title:

            flash(
                "Quiz title is required."
            )

            return redirect(
                url_for("create_quiz")
            )


        if duration_minutes <= 0:

            duration_minutes = 30


        if max_attempts <= 0:

            max_attempts = 1


        conn = get_db()

        try:

            quiz = conn.execute("""
                INSERT INTO quizzes
                (
                    teacher_id,
                    title,
                    description,
                    duration_minutes,
                    max_attempts,
                    randomize_questions,
                    randomize_options,
                    show_result_immediately,
                    published
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    FALSE
                )
                RETURNING id
            """, (
                session["user_id"],
                title,
                description,
                duration_minutes,
                max_attempts,
                randomize_questions,
                randomize_options,
                show_result_immediately
            )).fetchone()


            conn.commit()


        finally:

            conn.close()


        return redirect(
            url_for(
                "upload_questions",
                quiz_id=quiz["id"]
            )
        )


    return render_template(
        "create_quiz.html"
    )


# ============================================================
# EDIT QUIZ
# ============================================================

@app.route(
    "/teacher/quiz/<int:quiz_id>/edit",
    methods=["GET", "POST"]
)
@teacher_required
def edit_quiz(quiz_id):

    conn = get_db()

    try:

        quiz = conn.execute("""
            SELECT *
            FROM quizzes
            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        )).fetchone()


        if not quiz:

            flash(
                "Quiz not found."
            )

            return redirect(
                url_for("teacher_dashboard")
            )


        questions = conn.execute("""
            SELECT *
            FROM questions
            WHERE quiz_id = %s
            ORDER BY id
        """, (
            quiz_id,
        )).fetchall()


        if request.method == "POST":

            title = request.form.get(
                "title",
                ""
            ).strip()

            description = request.form.get(
                "description",
                ""
            ).strip()


            duration_minutes = request.form.get(
                "duration_minutes",
                30,
                type=int
            )

            max_attempts = request.form.get(
                "max_attempts",
                1,
                type=int
            )


            randomize_questions = (
                request.form.get(
                    "randomize_questions"
                ) == "on"
            )


            randomize_options = (
                request.form.get(
                    "randomize_options"
                ) == "on"
            )


            show_result_immediately = (
                request.form.get(
                    "show_result_immediately"
                ) == "on"
            )


            if not title:

                flash(
                    "Quiz title is required."
                )

                return redirect(
                    url_for(
                "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            if duration_minutes <= 0:

                duration_minutes = 30


            if max_attempts <= 0:

                max_attempts = 1


            conn.execute("""
                UPDATE quizzes

                SET
                    title = %s,
                    description = %s,
                    duration_minutes = %s,
                    max_attempts = %s,
                    randomize_questions = %s,
                    randomize_options = %s,
                    show_result_immediately = %s

                WHERE id = %s
                AND teacher_id = %s
            """, (
                title,
                description,
                duration_minutes,
                max_attempts,
                randomize_questions,
                randomize_options,
                show_result_immediately,
                quiz_id,
                session["user_id"]
            ))


            # ------------------------------------------------
            # Update every question
            # ------------------------------------------------

            for question in questions:

                question_id = question["id"]

                question_text = request.form.get(
                    f"question_{question_id}",
                    ""
                ).strip()

                option_a = request.form.get(
                    f"option_a_{question_id}",
                    ""
                ).strip()

                option_b = request.form.get(
                    f"option_b_{question_id}",
                    ""
                ).strip()

                option_c = request.form.get(
                    f"option_c_{question_id}",
                    ""
                ).strip()

                option_d = request.form.get(
                    f"option_d_{question_id}",
                    ""
                ).strip()

                correct_answer = request.form.get(
                    f"correct_answer_{question_id}",
                    ""
                ).upper().strip()


                if correct_answer not in [
                    "A",
                    "B",
                    "C",
                    "D"
                ]:

                    correct_answer = "A"


                conn.execute("""
                    UPDATE questions

                    SET
                        question = %s,
                        option_a = %s,
                        option_b = %s,
                        option_c = %s,
                        option_d = %s,
                        correct_answer = %s

                    WHERE id = %s
                    AND quiz_id = %s
                """, (
                    question_text,
                    option_a,
                    option_b,
                    option_c,
                    option_d,
                    correct_answer,
                    question_id,
                    quiz_id
                ))


            conn.commit()


            flash(
                "Quiz updated successfully."
            )


            return redirect(
                url_for(
                "upload_questions",
                    quiz_id=quiz_id
                )
            )


    finally:

        conn.close()


    return render_template(
        "edit_quiz.html",
        quiz=quiz,
        questions=questions
    )


# ============================================================
# PUBLISH QUIZ
# ============================================================

@app.route(
    "/teacher/quiz/<int:quiz_id>/publish",
    methods=["POST"]
)
@teacher_required
def publish_quiz(quiz_id):

    conn = get_db()

    try:

        quiz = conn.execute("""
            SELECT id
            FROM quizzes

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        )).fetchone()


        if not quiz:

            flash(
                "Quiz not found."
            )

            return redirect(
                url_for("teacher_dashboard")
            )


        # -----------------------------------------------
        # Make sure the quiz has questions
        # -----------------------------------------------

        question_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM questions
            WHERE quiz_id = %s
        """, (
            quiz_id,
        )).fetchone()["count"]


        if question_count == 0:

            flash(
                "Cannot publish a quiz with no questions."
            )

            return redirect(
                url_for("teacher_dashboard")
            )


        conn.execute("""
            UPDATE quizzes

            SET published = TRUE

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        ))


        conn.commit()


        flash(
            "Quiz published successfully."
        )


    finally:

        conn.close()


    return redirect(
        url_for("teacher_dashboard")
    )


# ============================================================
# UNPUBLISH QUIZ
# ============================================================

@app.route(
    "/teacher/quiz/<int:quiz_id>/unpublish",
    methods=["POST"]
)
@teacher_required
def unpublish_quiz(quiz_id):

    conn = get_db()

    try:

        conn.execute("""
            UPDATE quizzes

            SET published = FALSE

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        ))


        conn.commit()


        flash(
            "Quiz unpublished successfully."
        )


    finally:

        conn.close()


    return redirect(
        url_for("teacher_dashboard")
    )


# ============================================================
# DELETE QUIZ
# ============================================================

@app.route(
    "/teacher/quiz/<int:quiz_id>/delete",
    methods=["POST"]
)
@teacher_required
def delete_quiz(quiz_id):

    conn = get_db()

    try:

        conn.execute("""
            DELETE FROM quizzes

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        ))


        conn.commit()


    finally:

        conn.close()


    flash(
        "Quiz deleted successfully."
    )


    return redirect(
        url_for("teacher_dashboard")
    )


# ============================================================
# UPLOAD QUESTIONS FROM PDF
# ============================================================

@app.route(
    "/teacher/upload-questions/<int:quiz_id>",
    methods=["GET", "POST"]
)
@teacher_required
def upload_questions(quiz_id):

    conn = get_db()

    try:

        quiz = conn.execute("""
            SELECT *
            FROM quizzes

            WHERE id = %s
            AND teacher_id = %s
        """, (
            quiz_id,
            session["user_id"]
        )).fetchone()


        if not quiz:

            flash(
                "Quiz not found."
            )

            return redirect(
                url_for("teacher_dashboard")
            )


    finally:

        conn.close()


    # ========================================================
    # STEP 1: PDF UPLOAD
    # ========================================================

    if request.method == "POST":

        pdf_file = request.files.get(
            "pdf_file"
        )


        if pdf_file and pdf_file.filename:

            filename = os.path.basename(
                pdf_file.filename
            )


            if not filename.lower().endswith(".pdf"):

                flash(
                    "Please upload a PDF file."
                )

                return redirect(
                    url_for(
                        "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            unique_filename = (
                str(uuid.uuid4())
                + "_"
                + filename
            )


            filepath = os.path.join(
                UPLOAD_FOLDER,
                unique_filename
            )


            pdf_file.save(
                filepath
            )


            extracted_text = ""


            try:

                document = fitz.open(
                    filepath
                )


                for page in document:

                    extracted_text += (
                        page.get_text()
                        + "\n"
                    )


                document.close()


            except Exception as e:

                flash(
                    f"Could not read PDF: {e}"
                )

                return redirect(
                    url_for(
                        "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            # ------------------------------------------------
            # Extract questions and options
            # ------------------------------------------------

            questions = []

            current_question = None

            current_option = None


            lines = extracted_text.splitlines()


            for raw_line in lines:

                line = raw_line.strip()


                if not line:

                    continue


                # Question pattern
                question_match = re.match(
                    r"^\s*(\d+)[\.\)]\s*(.+)$",
                    line
                )


                if question_match:

                    if current_question:

                        questions.append(
                            current_question
                        )


                    current_question = {

                        "question":
                            question_match.group(2).strip(),

                        "option_a": "",

                        "option_b": "",

                        "option_c": "",

                        "option_d": "",

                        "correct_answer": "A"

                    }


                    current_option = None

                    continue


                # Option A-D
                option_match = re.match(
                    r"^\s*([A-Da-d])[\.\)]\s*(.+)$",
                    line
                )


                if option_match and current_question:

                    letter = (
                        option_match.group(1)
                        .upper()
                    )

                    option_text = (
                        option_match.group(2)
                        .strip()
                    )


                    current_question[
                        f"option_{letter.lower()}"
                    ] = option_text


                    current_option = letter

                    continue


                # Continuation line
                if current_question:

                    if current_option:

                        key = (
                            f"option_"
                            f"{current_option.lower()}"
                        )

                        current_question[key] += (
                            " " + line
                        )

                    else:

                        current_question[
                            "question"
                        ] += " " + line


            if current_question:

                questions.append(
                    current_question
                )


            if not questions:

                flash(
                    "No questions could be extracted from the PDF."
                )

                return redirect(
                    url_for(
                        "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            return render_template(
                "upload_questions.html",
                quiz=quiz,
                questions=questions
            )


    # ========================================================
    # STEP 2: SAVE REVIEWED QUESTIONS
    # ========================================================

    if request.method == "POST":

        # If no PDF was uploaded, the form is the
        # reviewed-question submission.

        if not request.files.get(
            "pdf_file"
        ):

            try:

                question_count = int(
                    request.form.get(
                        "question_count",
                        0
                    )
                )


            except ValueError:

                question_count = 0


            if question_count <= 0:

                flash(
                    "No questions were submitted."
                )

                return redirect(
                    url_for(
                        "upload_questions",
                        quiz_id=quiz_id
                    )
                )


            conn = get_db()

            try:

                for i in range(
                    question_count
                ):

                    question_text = request.form.get(
                        f"question_{i}",
                        ""
                    ).strip()

                    option_a = request.form.get(
                        f"option_a_{i}",
                        ""
                    ).strip()

                    option_b = request.form.get(
                        f"option_b_{i}",
                        ""
                    ).strip()

                    option_c = request.form.get(
                        f"option_c_{i}",
                        ""
                    ).strip()

                    option_d = request.form.get(
                        f"option_d_{i}",
                        ""
                    ).strip()

                    correct_answer = request.form.get(
                        f"correct_answer_{i}",
                        "A"
                    ).upper().strip()


                    if not question_text:

                        continue


                    if correct_answer not in [
                        "A",
                        "B",
                        "C",
                        "D"
                    ]:

                        correct_answer = "A"


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

                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                    """, (
                        quiz_id,
                        question_text,
                        option_a,
                        option_b,
                        option_c,
                        option_d,
                        correct_answer
                    ))


                conn.commit()


            finally:

                conn.close()


            flash(
                "Questions saved successfully."
            )


            return redirect(
                url_for(
                "upload_questions",
                    quiz_id=quiz_id
                )
            )


    return render_template(
        "upload_questions.html",
        quiz=quiz
    )


# ============================================================
# STUDENT DASHBOARD
# ============================================================

@app.route("/student/dashboard")
@student_required
def student_dashboard():
    conn = get_db()

    try:
        quizzes = conn.execute("""
            SELECT
                quizzes.id,
                quizzes.title,
                quizzes.description,
                quizzes.duration_minutes,
                quizzes.max_attempts,
                COUNT(questions.id) AS question_count
            FROM quizzes
            LEFT JOIN questions
                ON quizzes.id = questions.quiz_id
            WHERE quizzes.published = TRUE
            GROUP BY quizzes.id
            ORDER BY quizzes.id DESC
        """).fetchall()

        results = conn.execute("""
            SELECT
                attempts.id,
                quizzes.title,
                attempts.score,
                attempts.total,
                attempts.submitted_at
            FROM attempts
            JOIN quizzes
                ON attempts.quiz_id = quizzes.id
            WHERE attempts.student_id = %s
            ORDER BY attempts.submitted_at DESC
        """, (
            session["user_id"],
        )).fetchall()

    finally:
        conn.close()

    return render_template(
        "student_dashboard.html",
        quizzes=quizzes,
        results=results
    )

# ============================================================
# TAKE QUIZ
# ============================================================

@app.route(
    "/student/quiz/<int:quiz_id>",
    methods=["GET", "POST"]
)
@student_required
def take_quiz(quiz_id):

    conn = get_db()

    try:

        quiz = conn.execute("""
            SELECT *
            FROM quizzes

            WHERE id = %s
            AND published = TRUE
        """, (
            quiz_id,
        )).fetchone()


        if not quiz:

            flash(
                "Quiz not found or is not published."
            )

            return redirect(
                url_for("student_dashboard")
            )


        questions = conn.execute("""
            SELECT *
            FROM questions

            WHERE quiz_id = %s

            ORDER BY id
        """, (
            quiz_id,
        )).fetchall()


        if not questions:

            flash(
                "This quiz has no questions."
            )

            return redirect(
                url_for("student_dashboard")
            )


        # ----------------------------------------------------
        # Count attempts
        # ----------------------------------------------------

        attempt_count = conn.execute("""
            SELECT COUNT(*) AS count
            FROM attempts

            WHERE quiz_id = %s
            AND student_id = %s
        """, (
            quiz_id,
            session["user_id"]
        )).fetchone()["count"]


        max_attempts = quiz[
            "max_attempts"
        ] or 1


        # ----------------------------------------------------
        # Check attempt limit
        # ----------------------------------------------------

        if attempt_count >= max_attempts:

            flash(
                "You have used all your allowed attempts."
            )

            return redirect(
                url_for("student_dashboard")
            )


        # ----------------------------------------------------
        # SUBMIT QUIZ
        # ----------------------------------------------------


        if request.method == "POST":

            score = 0
            total = len(questions)
            answer_records = []

            for question in questions:
                selected_answer = request.form.get(
                    f"question_{question['id']}"
                )

                correct_answer = question["correct_answer"]
                is_correct = selected_answer == correct_answer

                if is_correct:
                    score += 1

                answer_records.append((
                    question["id"],
                    question["question"],
                    selected_answer,
                    correct_answer,
                    is_correct
                ))

            attempt = conn.execute("""
                    INSERT INTO attempts
                    (
                        quiz_id,
                        student_id,
                        score,
                        total
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                """, (
                quiz_id,
                session["user_id"],
                score,
                total
            )).fetchone()

            attempt_id = attempt["id"]

            for answer in answer_records:
                conn.execute("""
                        INSERT INTO attempt_answers
                        (
                            attempt_id,
                            question_id,
                            question_text,
                            selected_answer,
                            correct_answer,
                            is_correct
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                    attempt_id,
                    answer[0],
                    answer[1],
                    answer[2],
                    answer[3],
                    answer[4]
                ))

            conn.commit()

            if quiz["show_result_immediately"]:
                return render_template(
                    "quiz_result.html",
                    quiz=quiz,
                    score=score,
                    total=total
                )

            flash("Quiz submitted successfully.")
            return redirect(url_for("student_dashboard"))





    finally:

        conn.close()


    return render_template(
        "take_quiz.html",
        quiz=quiz,
        questions=questions
    )


# ============================================================
# STUDENT RESULTS
# ============================================================

@app.route("/student/results")
@student_required
def student_results():

    conn = get_db()

    try:

        results = conn.execute("""
            SELECT
                attempts.id,
                quizzes.title,
                attempts.score,
                attempts.total,
                attempts.submitted_at
            FROM attempts

            JOIN quizzes
                ON attempts.quiz_id = quizzes.id

            WHERE attempts.student_id = %s

            ORDER BY attempts.submitted_at DESC
        """, (
            session["user_id"],
        )).fetchall()


    finally:

        conn.close()


    return render_template(
        "student_results.html",
        results=results
    )


# ============================================================
# TEACHER RESULTS
# ============================================================

@app.route("/teacher/results")
@teacher_required
def teacher_results():

    quiz_id = request.args.get(
        "quiz_id",
        type=int
    )


    conn = get_db()

    try:

        if quiz_id:

            results = conn.execute("""
                                SELECT
                    attempts.id,
                    quizzes.title,
                    users.full_name,
                    users.username,
                    attempts.score,
                    attempts.total,
                    attempts.submitted_at

                FROM attempts

                JOIN quizzes
                    ON attempts.quiz_id = quizzes.id

                JOIN users
                    ON attempts.student_id = users.id

                WHERE quizzes.teacher_id = %s
                AND quizzes.id = %s

                ORDER BY attempts.submitted_at DESC
            """, (
                session["user_id"],
                quiz_id
            )).fetchall()


        else:

            results = conn.execute("""
                SELECT
                    attempts.id,
                    quizzes.title,
                    users.username AS student,
                    attempts.score,
                    attempts.total,
                    attempts.submitted_at

                FROM attempts

                JOIN quizzes
                    ON attempts.quiz_id = quizzes.id

                JOIN users
                    ON attempts.student_id = users.id

                WHERE quizzes.teacher_id = %s

                ORDER BY attempts.submitted_at DESC
            """, (
                session["user_id"],
            )).fetchall()


    finally:

        conn.close()


    return render_template(
        "teacher_results.html",
        results=results,
        quiz_id=quiz_id
    )

# ============================================================
# DOWNLOAD STUDENT QUIZ REPORT AS PDF
# ============================================================

@app.route("/teacher/attempt/<int:attempt_id>/report.pdf")
@teacher_required
def download_attempt_report(attempt_id):

    conn = get_db()

    try:
        # Verify that this attempt belongs to a quiz
        # owned by the logged-in teacher.
        attempt = conn.execute("""
            SELECT
                attempts.id,
                attempts.score,
                attempts.total,
                attempts.submitted_at,
                quizzes.title AS quiz_title,
                users.full_name,
                users.username

            FROM attempts

            JOIN quizzes
                ON attempts.quiz_id = quizzes.id

            JOIN users
                ON attempts.student_id = users.id

            WHERE attempts.id = %s
            AND quizzes.teacher_id = %s
        """, (
            attempt_id,
            session["user_id"]
        )).fetchone()

        if not attempt:
            flash("Report not found or access denied.")
            return redirect(url_for("teacher_results"))

        # Retrieve the answers saved when the student
        # submitted the quiz.
        answers = conn.execute("""
            SELECT
                attempt_answers.question_text,
                attempt_answers.selected_answer,
                attempt_answers.correct_answer,
                attempt_answers.is_correct,
                questions.option_a,
                questions.option_b,
                questions.option_c,
                questions.option_d

            FROM attempt_answers

            LEFT JOIN questions
                ON attempt_answers.question_id = questions.id

            WHERE attempt_answers.attempt_id = %s

            ORDER BY attempt_answers.id
        """, (
            attempt_id,
        )).fetchall()

    finally:
        conn.close()

    # Build the PDF in memory.
    pdf_buffer = BytesIO()

    document = SimpleDocTemplate(
        pdf_buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm
    )

    styles = getSampleStyleSheet()
    story = []

    from xml.sax.saxutils import escape

    def safe_text(value):
        if value is None or value == "":
            return "Not answered"
        return escape(str(value))

    def answer_text(letter, answer):
        if not letter:
            return "Not answered"

        letter = str(letter).strip().upper()

        options = {
            "A": answer["option_a"],
            "B": answer["option_b"],
            "C": answer["option_c"],
            "D": answer["option_d"]
        }

        option = options.get(letter)

        if option is not None:
            return f"{letter} - {safe_text(option)}"

        return safe_text(letter)

    story.append(Paragraph(
        "STOCKWELL MONTESSORI SCHOOL",
        styles["Title"]
    ))

    story.append(Paragraph(
        "Student Quiz Report",
        styles["Heading1"]
    ))

    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph(
        f"<b>Student:</b> {safe_text(attempt['full_name'])}",
        styles["Normal"]
    ))

    story.append(Paragraph(
        f"<b>Username:</b> {safe_text(attempt['username'])}",
        styles["Normal"]
    ))

    story.append(Paragraph(
        f"<b>Quiz:</b> {safe_text(attempt['quiz_title'])}",
        styles["Normal"]
    ))

    story.append(Paragraph(
        f"<b>Submission date:</b> "
        f"{safe_text(attempt['submitted_at'])}",
        styles["Normal"]
    ))

    score = attempt["score"] or 0
    total = attempt["total"] or 0

    percentage = (score / total * 100) if total else 0

    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph(
        f"<b>Score:</b> {score}/{total} "
        f"({percentage:.1f}%)",
        styles["Heading2"]
    ))

    story.append(Spacer(1, 5 * mm))

    table_data = [[
        "Question",
        "Student's Answer",
        "Correct Answer",
        "Result"
    ]]

    for answer in answers:

        selected = answer_text(
            answer["selected_answer"],
            answer
        )

        correct = answer_text(
            answer["correct_answer"],
            answer
        )

        status = (
            "CORRECT"
            if answer["is_correct"]
            else "WRONG"
        )

        question = Paragraph(
            safe_text(answer["question_text"]),
            styles["BodyText"]
        )

        selected_paragraph = Paragraph(
            selected,
            styles["BodyText"]
        )

        correct_paragraph = Paragraph(
            correct,
            styles["BodyText"]
        )

        status_paragraph = Paragraph(
            status,
            styles["BodyText"]
        )

        table_data.append([
            question,
            selected_paragraph,
            correct_paragraph,
            status_paragraph
        ])

    if len(table_data) == 1:
        table_data.append([
            "No saved answer details were found.",
            "",
            "",
            ""
        ])

    report_table = Table(
        table_data,
        colWidths=[
            65 * mm,
            40 * mm,
            40 * mm,
            25 * mm
        ],
        repeatRows=1
    )

    report_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#163A5F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
    ]))

    story.append(report_table)

    document.build(story)

    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"student_quiz_report_{attempt_id}.pdf"
    )
# ============================================================
# MESSAGES
# ============================================================

@app.route("/messages")
def messages():

    if "user_id" not in session:

        return redirect(
            url_for("login")
        )


    conn = get_db()

    try:

        messages = conn.execute("""
            SELECT
                messages.id,
                messages.subject,
                messages.message,
                messages.created_at,
                messages.is_read,
                users.username AS sender
            FROM messages

            JOIN users
                ON messages.sender_id = users.id

            WHERE messages.receiver_id = %s

            ORDER BY messages.created_at DESC
        """, (
            session["user_id"],
        )).fetchall()


    finally:

        conn.close()


    return render_template(
        "messages.html",
        messages=messages
    )


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    try:

        print(
            "Initializing database..."
        )

        init_db()

        print(
            "Database ready."
        )


        print(
            "Starting Flask website..."
        )


        app.run(
            debug=True,
            use_reloader=False
        )


    except Exception as e:

        print(
            "\nDATABASE / APPLICATION ERROR:"
        )

        print(
            e
        )

        raise