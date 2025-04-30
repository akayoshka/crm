from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import login_required, current_user
from werkzeug.exceptions import NotFound
from datetime import datetime
from sqlalchemy import or_

from app import db
from forms import TaskForm, DocumentUploadForm, MessageForm
from models import User, Task, TaskStatus, Route, Document, Message, UserRole, ActionType, Company, Operator
from services import TaskService, MessageService
from utils import role_required, company_access_required, log_action, save_document

from app import db
from forms import TaskForm, DocumentUploadForm, MessageForm
from models import User, Task, TaskStatus, Route, Document, Message, UserRole, ActionType
from services import TaskService, MessageService
from utils import role_required, company_access_required, log_action
from flask_wtf import FlaskForm
from wtforms import (
    StringField, PasswordField, BooleanField, SubmitField,
    SelectField, TextAreaField, FileField, DateTimeField,
    IntegerField, FloatField
)
from wtforms.validators import (
    DataRequired, Email, EqualTo, Length,
    ValidationError, Optional, Regexp
)
from flask_wtf.file import FileAllowed

from models import User, UserRole, TaskStatus, RouteStatus

tasks = Blueprint('tasks', __name__, url_prefix='/tasks')


@tasks.route('/')
@login_required
def list_tasks():
    """
    List tasks with filters based on user role
    """
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', None)
    search_term = request.args.get('search', '')
    now = datetime.utcnow()  # Add this line to define 'now'

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # Admin can see all tasks if no company filter
    if current_user.role == UserRole.ADMIN and not company_id:
        company_id = request.args.get('company_id', None, type=int)

    # Apply role-specific filters
    creator_id = None
    assignee_id = None

    if current_user.role == UserRole.DRIVER:
        # Drivers can only see tasks assigned to them
        assignee_id = current_user.id
    elif current_user.role == UserRole.OPERATOR:
        # Operators see tasks they created or that are assigned to their drivers
        if request.args.get('view', '') != 'all':
            creator_id = current_user.id

    # Convert status string to enum if provided
    task_status = None
    if status:
        try:
            task_status = TaskStatus(status)
        except ValueError:
            # Invalid status value, ignore filter
            pass

    # Search tasks
    tasks = TaskService.search_tasks(
        search_term, company_id, task_status, creator_id, assignee_id, page
    )

    # Get available statuses for filtering
    statuses = [status.value for status in TaskStatus]

    log_action(ActionType.VIEW, "Viewed task list", db)

    return render_template(
        'tasks/list_tasks.html',
        title='Tasks',
        tasks=tasks,
        search_term=search_term,
        current_status=status,
        statuses=statuses,
        now=now  # Pass 'now' to the template
    )


@tasks.route('/create', methods=['GET', 'POST'])
@login_required
@role_required(["ADMIN", "COMPANY_OWNER", "MANAGER", "OPERATOR"])
def create_task():
    """
    Create a new task
    """
    form = TaskForm()

    # Ensure all SelectField choices are initialized to prevent 'Choices cannot be None' error
    if form.assignee_id.choices is None:
        form.assignee_id.choices = []
    if hasattr(form, 'status') and form.status.choices is None:
        form.status.choices = [(s.value, s.name) for s in TaskStatus]
    if hasattr(form, 'company_id') and form.company_id.choices is None:
        form.company_id.choices = []

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.ADMIN:
        # Admin must select a company
        form.company_id.choices = [(c.id, c.name) for c in Company.query.all()]
        if form.validate_on_submit():
            company_id = form.company_id.data

    # If no company ID, redirect to dashboard
    if not company_id and current_user.role != UserRole.ADMIN:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    # Get available drivers for assignment
    drivers = []

    # For operators, only their drivers
    if current_user.role == UserRole.OPERATOR and current_user.operator:
        drivers = [
            (d.user.id, f"{d.user.first_name} {d.user.last_name}")
            for d in current_user.operator.drivers if d.user is not None
        ]
    # For managers, only drivers under their operators
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        # Get operators under this manager
        operators = Operator.query.filter_by(manager_id=current_user.id).all()

        # Get drivers from these operators
        for operator in operators:
            for driver in operator.drivers:
                if driver.user:  # Make sure user exists
                    drivers.append((driver.user.id, f"{driver.user.first_name} {driver.user.last_name}"))
    # For admins and company owners, all drivers in the company
    else:
        drivers_query = User.query.filter_by(role=UserRole.DRIVER)
        driver_users = drivers_query.all()
        for driver_user in driver_users:
            if hasattr(driver_user, 'driver') and driver_user.driver and driver_user.driver.company_id == company_id:
                drivers.append((driver_user.id, f"{driver_user.first_name} {driver_user.last_name}"))

    # Add empty option
    drivers.insert(0, (0, 'Select a driver'))
    form.assignee_id.choices = drivers

    if form.validate_on_submit():
        try:
            task = Task(
                title=form.title.data,
                description=form.description.data,
                status=TaskStatus.NEW,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                deadline=form.deadline.data,
                company_id=company_id,
                creator_id=current_user.id,
                assignee_id=form.assignee_id.data if form.assignee_id.data != 0 else None
            )

            db.session.add(task)
            db.session.commit()

            # Handle document upload if provided
            if form.document.data:
                file = form.document.data
                # Save document file and get file info
                file_path, file_type, file_size = save_document(file, task.id, task.company_id)

                if file_path:
                    # Create document record with a string category
                    document = Document(
                        title=file.filename,
                        file_path=file_path,
                        file_type=file_type,
                        size=file_size,
                        uploaded_at=datetime.utcnow(),
                        uploader_id=current_user.id,
                        task_id=task.id,
                        company_id=task.company_id,
                        document_category='task'
                    )

                    db.session.add(document)
                    db.session.commit()

            log_action(ActionType.CREATE, f"Created task {task.title}", db)
            flash(f'Task "{task.title}" created successfully!', 'success')
            return redirect(url_for('tasks.view_task', task_id=task.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating task: {str(e)}', 'danger')

    return render_template('tasks/create_task.html', title='Create Task', form=form)


@tasks.route('/<int:task_id>')
@login_required
def view_task(task_id):
    """
    View task details
    """
    task = Task.query.get_or_404(task_id)

    # Check if user has access to this task
    if not _can_access_task(task):
        flash('You do not have permission to access this task.', 'danger')
        return redirect(url_for('tasks.list_tasks'))

    # Check if user can edit this task
    can_edit = _can_edit_task(task)

    # Forms for document upload and messaging
    upload_form = DocumentUploadForm()
    message_form = MessageForm()

    log_action(ActionType.VIEW, f"Viewed task {task.title}", db)

    # Add current time for template
    now = datetime.utcnow()

    return render_template(
        'tasks/view_task.html',
        title=f'Task: {task.title}',
        task=task,
        upload_form=upload_form,
        message_form=message_form,
        now=now,
        can_edit=can_edit  # Pass this parameter to template
    )


@tasks.route('/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(task_id):
    """
    Delete a task
    """
    task = Task.query.get_or_404(task_id)

    # Check if user has access to delete this task
    if not _can_delete_task(task):
        flash('You do not have permission to delete this task.', 'danger')
        return redirect(url_for('tasks.view_task', task_id=task.id))

    try:
        task_title = task.title
        db.session.delete(task)
        db.session.commit()
        log_action(ActionType.DELETE, f"Deleted task {task_title}", db)
        flash(f'Task "{task_title}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting task: {str(e)}', 'danger')

    return redirect(url_for('tasks.list_tasks'))


@tasks.route('/<int:task_id>/complete', methods=['POST'])
@login_required
def complete_task(task_id):
    """
    Mark a task as completed
    """
    task = Task.query.get_or_404(task_id)

    # Check if user has access to complete this task
    if not _can_complete_task(task):
        flash('You do not have permission to complete this task.', 'danger')
        return redirect(url_for('tasks.view_task', task_id=task.id))

    try:
        task.status = TaskStatus.COMPLETED
        task.updated_at = datetime.utcnow()
        db.session.commit()
        log_action(ActionType.UPDATE, f"Marked task {task.title} as completed", db)
        flash(f'Task "{task.title}" marked as completed!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error completing task: {str(e)}', 'danger')

    return redirect(url_for('tasks.view_task', task_id=task.id))


@tasks.route('/<int:task_id>/upload', methods=['POST'])
@login_required
def upload_document(task_id):
    """Upload document for a task"""
    task = Task.query.get_or_404(task_id)

    # Check permissions (simplified example)
    if not (current_user.id == task.creator_id or current_user.id == task.assignee_id):
        flash('You do not have permission to upload documents to this task.', 'danger')
        return redirect(url_for('tasks.view_task', task_id=task_id))

    form = DocumentUploadForm()

    if form.validate_on_submit():
        # Process file upload
        file = form.document.data

        # Save document file and get file info
        file_path, file_type, file_size = save_document(file, task_id, task.company_id)

        if not file_path:
            flash('Error saving document. Please check file type and size.', 'danger')
            return redirect(url_for('tasks.view_task', task_id=task_id))

        try:
            # Create document record
            document = Document(
                title=form.title.data,
                file_path=file_path,
                file_type=file_type,
                size=file_size,
                uploaded_at=datetime.utcnow(),
                uploader_id=current_user.id,
                task_id=task_id,
                company_id=task.company_id,
                document_category='task'  # Строковое значение вместо перечисления
            )

            # Добавляем в сессию и сохраняем
            db.session.add(document)
            db.session.commit()

            # Логируем действие
            log_action(ActionType.UPLOAD, f"Uploaded document: {form.title.data}", db)
            flash('Document uploaded successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error creating document record: {str(e)}")
            flash(f'Error creating document record: {str(e)}', 'danger')

    return redirect(url_for('tasks.view_task', task_id=task_id))


@tasks.route('/documents/<int:document_id>/delete', methods=['POST'])
@login_required
def delete_document(document_id):
    """
    Delete a document
    """
    document = Document.query.get_or_404(document_id)
    task_id = document.task_id

    # Check if user has access to delete this document
    if not _can_delete_document(document):
        flash('You do not have permission to delete this document.', 'danger')
        return redirect(url_for('tasks.view_task', task_id=task_id))

    try:
        document_title = document.title
        db.session.delete(document)
        db.session.commit()
        log_action(ActionType.DELETE, f"Deleted document {document_title}", db)
        flash(f'Document "{document_title}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting document: {str(e)}', 'danger')

    return redirect(url_for('tasks.view_task', task_id=task_id))


@tasks.route('/<int:task_id>/send-message', methods=['POST'])
@login_required
def send_message(task_id):
    """
    Send a message in a task
    """
    task = Task.query.get_or_404(task_id)

    # Check if user has access to this task
    if not _can_access_task(task):
        flash('You do not have permission to access this task.', 'danger')
        return redirect(url_for('tasks.list_tasks'))

    form = MessageForm()

    if form.validate_on_submit():
        try:
            # Determine recipient based on roles
            recipient_id = None

            if current_user.role == UserRole.DRIVER and task.creator:
                # Driver -> Creator (usually operator)
                recipient_id = task.creator_id
            elif current_user.role in [UserRole.OPERATOR, UserRole.MANAGER, UserRole.COMPANY_OWNER,
                                       UserRole.ADMIN] and task.assignee:
                # Staff -> Assigned driver
                recipient_id = task.assignee_id

            if recipient_id:
                message = MessageService.send_message(
                    current_user.id, recipient_id, task.id, form.content.data, task.company_id, db
                )
                flash('Message sent successfully!', 'success')
            else:
                flash('Could not determine message recipient.', 'danger')
        except Exception as e:
            flash(f'Error sending message: {str(e)}', 'danger')

    return redirect(url_for('tasks.view_task', task_id=task.id))


# Helper functions
def _can_access_task(task):
    """
    Check if current user can access a task
    """
    # Admin can access any task
    if current_user.role == UserRole.ADMIN:
        return True

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.DRIVER and current_user.driver:
        company_id = current_user.driver.company_id

    # Must be in same company
    if task.company_id != company_id:
        return False

    # Drivers can only see tasks assigned to them
    if current_user.role == UserRole.DRIVER and task.assignee_id != current_user.id:
        return False

    return True


def _can_edit_task(task):
    """
    Check if current user can edit a task
    """
    # Admin, company owner, and creator can edit
    if current_user.role == UserRole.ADMIN:
        return True

    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and task.company_id == current_user.company_owner.company_id:
        return True

    if task.creator_id == current_user.id:
        return True

    # Manager can edit any task in their company
    if current_user.role == UserRole.MANAGER and current_user.manager and task.company_id == current_user.manager.company_id:
        return True

    return False


def _can_delete_task(task):
    """
    Check if current user can delete a task
    """
    # Only admin, company owner, and creator can delete
    if current_user.role == UserRole.ADMIN:
        return True

    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and task.company_id == current_user.company_owner.company_id:
        return True

    if task.creator_id == current_user.id:
        return True

    return False


def _can_complete_task(task):
    """
    Check if current user can mark a task as completed
    """
    # Admin, company owner, creator, assignee can complete
    if current_user.role == UserRole.ADMIN:
        return True

    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and task.company_id == current_user.company_owner.company_id:
        return True

    if task.creator_id == current_user.id:
        return True

    if task.assignee_id == current_user.id:
        return True

    # Manager can complete any task in their company
    if current_user.role == UserRole.MANAGER and current_user.manager and task.company_id == current_user.manager.company_id:
        return True

    return False


def _can_delete_document(document):
    """
    Check if current user can delete a document
    """
    # Admin can delete any document
    if current_user.role == UserRole.ADMIN:
        return True

    # Document uploader can delete
    if document.uploader_id == current_user.id:
        return True

    # Company owner can delete any document in their company
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner and document.company_id == current_user.company_owner.company_id:
        return True

    # Task creator can delete documents on their tasks
    task = document.task
    if task and task.creator_id == current_user.id:
        return True

    return False


@tasks.route('/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    """
    Edit an existing task
    """
    task = Task.query.get_or_404(task_id)

    # Check if user has access to edit this task
    if not _can_edit_task(task):
        flash('You do not have permission to edit this task.', 'danger')
        return redirect(url_for('tasks.view_task', task_id=task.id))

    # Create form
    form = TaskForm()

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id
    elif current_user.role == UserRole.ADMIN:
        company_id = task.company_id

    # Get available drivers for this company
    drivers = []
    if company_id:
        driver_query = User.query.filter_by(role=UserRole.DRIVER)

        # For operators, only show their drivers
        if current_user.role == UserRole.OPERATOR and current_user.operator:
            driver_ids = [d.id for d in current_user.operator.drivers]
            driver_query = driver_query.filter(User.id.in_(driver_ids))

        drivers = [(d.id, f"{d.first_name} {d.last_name}") for d in driver_query.all()]

    # Add empty option and set choices
    drivers.insert(0, (0, 'Select a driver'))
    form.assignee_id.choices = drivers

    if request.method == 'GET':
        # Fill form with current task data
        form.title.data = task.title
        form.description.data = task.description
        form.assignee_id.data = task.assignee_id if task.assignee_id else 0
        form.deadline.data = task.deadline
        if hasattr(form, 'status'):
            form.status.data = task.status.value

    if form.validate_on_submit():
        try:
            # Update task with form data
            task.title = form.title.data
            task.description = form.description.data

            # Update status if form includes status field
            if hasattr(form, 'status') and form.status.data:
                task.status = TaskStatus(form.status.data)

            # Update assignee
            task.assignee_id = form.assignee_id.data if form.assignee_id.data != 0 else None

            # Update deadline
            task.deadline = form.deadline.data

            # Update updated_at
            task.updated_at = datetime.utcnow()

            # Handle document upload if provided
            if form.document.data:
                TaskService.add_document(task, form.document.data, current_user.id, db)

            db.session.commit()
            log_action(ActionType.UPDATE, f"Updated task {task.title}", db)

            flash(f'Task "{task.title}" updated successfully!', 'success')
            return redirect(url_for('tasks.view_task', task_id=task.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating task: {str(e)}', 'danger')

    return render_template(
        'tasks/edit_task.html',
        title='Edit Task',
        form=form,
        task=task
    )