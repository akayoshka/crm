from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from sqlalchemy import func, or_, and_

from app import db
from models import (
    Route, RouteStatus, User, UserRole, Task, TaskStatus, Driver, Message,
    Document, Operator, ActionType, Manager, CompanyOwner, DocumentCategory
)
from forms import DocumentUploadForm, MessageForm
from utils import role_required, log_action
from werkzeug.utils import secure_filename
import os

driver = Blueprint('driver', __name__, url_prefix='/driver')


@driver.route('/dashboard')
@login_required
@role_required("DRIVER")
def routes_dashboard():
    """
    Driver dashboard showing routes
    """
    # Check if user is a driver
    if not current_user.driver:
        flash('This page is only accessible to drivers.', "DANGER")
        return redirect(url_for('main.index'))

    # Get driver's routes
    active_route = Route.query.filter_by(
        driver_id=current_user.driver.id,
        status=RouteStatus.IN_PROGRESS
    ).first()

    next_route = Route.query.filter_by(
        driver_id=current_user.driver.id,
        status=RouteStatus.PLANNED
    ).order_by(Route.start_time).first()

    upcoming_routes = Route.query.filter_by(
        driver_id=current_user.driver.id,
        status=RouteStatus.PLANNED
    ).order_by(Route.start_time).offset(1).limit(5).all()

    completed_routes = Route.query.filter_by(
        driver_id=current_user.driver.id,
        status=RouteStatus.COMPLETED
    ).order_by(Route.end_time.desc()).limit(5).all()

    # Get active and new tasks
    active_task = Task.query.filter_by(
        assignee_id=current_user.id,
        status=TaskStatus.IN_PROGRESS
    ).order_by(Task.deadline).first()

    new_tasks = Task.query.filter_by(
        assignee_id=current_user.id,
        status=TaskStatus.NEW
    ).order_by(Task.deadline).limit(3).all()

    # Get recent messages
    recent_messages = Message.query.filter_by(
        recipient_id=current_user.id
    ).order_by(Message.sent_at.desc()).limit(5).all()

    # Get unread message count
    unread_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator info
    operator = None
    if current_user.driver and current_user.driver.operator_id:
        operator_user = User.query.join(
            User.operator
        ).filter(
            User.operator.has(id=current_user.driver.operator_id)
        ).first()

        if operator_user:
            # Creating a SimpleNamespace-like object with attributes instead of dictionary
            # This makes it compatible with both dictionary-style access and attribute access
            class SimpleObject:
                def __init__(self, **kwargs):
                    for key, value in kwargs.items():
                        setattr(self, key, value)

            operator = SimpleObject(
                id=operator_user.id,
                name=f"{operator_user.first_name} {operator_user.last_name}",
                email=operator_user.email,
                phone=operator_user.phone
            )

    # Get driver stats
    stats = _get_driver_stats(current_user.driver.id)

    log_action(ActionType.VIEW, "Viewed driver dashboard", db)

    return render_template(
        'driver/dashboard.html',
        title='Driver Dashboard',
        active_route=active_route,
        next_route=next_route,
        upcoming_routes=upcoming_routes,
        completed_routes=completed_routes,
        active_task=active_task,
        new_tasks=new_tasks,
        recent_messages=recent_messages,
        unread_count=unread_count,
        operator=operator,
        stats=stats,
        now=datetime.utcnow()
    )


@driver.route('/tasks')
@login_required
@role_required("DRIVER")
def tasks():
    """
    Show tasks assigned to driver
    """
    # Get view filter (all, active, completed)
    view = request.args.get("VIEW", "ALL")

    # Get assigned tasks
    active_tasks = Task.query.filter_by(
        assignee_id=current_user.id,
        status=TaskStatus.IN_PROGRESS
    ).order_by(Task.deadline).all()

    new_tasks = Task.query.filter_by(
        assignee_id=current_user.id,
        status=TaskStatus.NEW
    ).order_by(Task.deadline).all()

    completed_tasks = Task.query.filter_by(
        assignee_id=current_user.id,
        status=TaskStatus.COMPLETED
    ).order_by(Task.updated_at.desc()).limit(5).all()

    # If view is "active", only show active tasks
    if view == 'active':
        active_tasks = active_tasks + new_tasks
        new_tasks = []
        completed_tasks = []
    # If view is "completed", only show completed tasks
    elif view == 'completed':
        completed_tasks = Task.query.filter_by(
            assignee_id=current_user.id,
            status=TaskStatus.COMPLETED
        ).order_by(Task.updated_at.desc()).all()
        active_tasks = []
        new_tasks = []

    # Get task stats
    stats = {
        'total': len(active_tasks) + len(new_tasks) + len(completed_tasks),
        'active': len(active_tasks),
        'new': len(new_tasks),
        'completed': len(completed_tasks),
        'overdue': sum(1 for t in active_tasks + new_tasks if t.deadline and t.deadline < datetime.utcnow())
    }

    log_action(ActionType.VIEW, "Viewed driver tasks", db)

    return render_template(
        'driver/tasks.html',
        title='My Tasks',
        active_tasks=active_tasks,
        new_tasks=new_tasks,
        completed_tasks=completed_tasks,
        stats=stats,
        view=view,
        now=datetime.utcnow()
    )


@driver.route('/profile')
@login_required
@role_required("DRIVER")
def profile():
    """
    Show driver profile
    """
    # Get operator info
    operator = None
    if current_user.driver and current_user.driver.operator_id:
        operator_user = User.query.join(
            User.operator
        ).filter(
            User.operator.has(id=current_user.driver.operator_id)
        ).first()

        if operator_user:
            # Using the same object structure as in the dashboard
            class SimpleObject:
                def __init__(self, **kwargs):
                    for key, value in kwargs.items():
                        setattr(self, key, value)

            operator = SimpleObject(
                id=operator_user.id,
                name=f"{operator_user.first_name} {operator_user.last_name}",
                email=operator_user.email,
                phone=operator_user.phone
            )

    # Get driver stats
    stats = _get_driver_stats(current_user.driver.id)

    log_action(ActionType.VIEW, "Viewed driver profile", db)

    return render_template(
        'driver/profile.html',
        title='Driver Profile',
        operator=operator,
        stats=stats
    )


@driver.route('/documents')
@login_required
@role_required("DRIVER")
def documents():
    """
    Show driver documents
    """
    page = request.args.get("page", 1, type=int)
    category = request.args.get("category", "all").lower()
    search_term = request.args.get("search", '')

    # Create document query
    query = Document.query

    # Filter by company
    if current_user.driver and current_user.driver.company_id:
        query = query.filter(Document.company_id == current_user.driver.company_id)

    # Filter by driver-specific access
    # Documents are visible if:
    # 1. The driver uploaded them
    # 2. They're attached to the driver's tasks
    # 3. They're attached to the driver's routes
    # 4. They're marked as accessible to this driver

    task_ids = [task.id for task in Task.query.filter_by(assignee_id=current_user.id).all()]
    route_ids = [route.id for route in Route.query.filter_by(driver_id=current_user.driver.id).all()]

    query = query.filter(
        or_(
            Document.uploader_id == current_user.id,
            Document.task_id.in_(task_ids) if task_ids else False,
            Document.route_id.in_(route_ids) if route_ids else False,
            Document.access_user_id == current_user.id
        )
    )

    # Apply category filter - using lowercase values to match database enum
    if category == 'task':
        query = query.filter(Document.document_category == 'task')
    elif category == 'route':
        query = query.filter(Document.document_category == 'route')
    elif category == 'personal':
        query = query.filter(Document.document_category == 'personal')
    elif category == 'vehicle':
        query = query.filter(Document.document_category == 'vehicle')
    elif category == 'other':
        query = query.filter(Document.document_category == 'other')

    # Apply search filter
    if search_term:
        query = query.filter(Document.title.ilike(f'%{search_term}%'))

    # Order by upload date (newest first)
    query = query.order_by(Document.uploaded_at.desc())

    # Paginate results
    documents = query.paginate(page=page, per_page=10)

    # Get document counts for sidebar - using lowercase strings for enum values
    document_counts = {
        'all': Document.query.filter(
            or_(
                Document.uploader_id == current_user.id,
                Document.task_id.in_(task_ids) if task_ids else False,
                Document.route_id.in_(route_ids) if route_ids else False,
                Document.access_user_id == current_user.id
            )
        ).count(),
        'task': Document.query.filter(
            Document.document_category == 'task',
            or_(
                Document.uploader_id == current_user.id,
                Document.task_id.in_(task_ids) if task_ids else False,
                Document.access_user_id == current_user.id
            )
        ).count(),
        'route': Document.query.filter(
            Document.document_category == 'route',
            or_(
                Document.uploader_id == current_user.id,
                Document.route_id.in_(route_ids) if route_ids else False,
                Document.access_user_id == current_user.id
            )
        ).count(),
        'personal': Document.query.filter(
            Document.document_category == 'personal',
            or_(
                Document.uploader_id == current_user.id,
                Document.access_user_id == current_user.id
            )
        ).count(),
        'vehicle': Document.query.filter(
            Document.document_category == 'vehicle',
            or_(
                Document.uploader_id == current_user.id,
                Document.access_user_id == current_user.id
            )
        ).count(),
        'other': Document.query.filter(
            Document.document_category == 'other',
            or_(
                Document.uploader_id == current_user.id,
                Document.access_user_id == current_user.id
            )
        ).count()
    }

    # Get active tasks for document upload form
    active_tasks = Task.query.filter(
        Task.assignee_id == current_user.id,
        Task.status.in_(['NEW', 'IN_PROGRESS'])  # Используем строковые значения в нижнем регистре
    ).all()

    # Get active routes for document upload form
    active_routes = Route.query.filter(
        Route.driver_id == current_user.driver.id,
        Route.status.in_(['PLANNED', 'IN_PROGRESS'])  # Используем строковые значения в нижнем регистре
    ).all()

    # Create upload form
    upload_form = DocumentUploadForm()

    log_action(ActionType.VIEW, "Viewed driver documents", db)

    return render_template(
        'driver/documents.html',
        title='My Documents',
        documents=documents,
        document_counts=document_counts,
        category=category,
        search_term=search_term,
        active_tasks=active_tasks,
        active_routes=active_routes,
        upload_form=upload_form
    )


@driver.route('/upload-document', methods=['POST'])
@login_required
@role_required("DRIVER")
def upload_document():
    """
    Upload a document
    """
    form = DocumentUploadForm()

    if form.validate_on_submit():
        try:
            # Get form data
            title = form.title.data
            document_file = form.document.data
            document_category_str = request.form.get("document_category", "personal").lower()
            task_id = request.form.get("task_id")
            route_id = request.form.get("route_id")

            # Convert task_id and route_id to int or None
            task_id = int(task_id) if task_id and task_id.isdigit() else None
            route_id = int(route_id) if route_id and route_id.isdigit() else None

            # Set document category based on context - используем строковые значения в нижнем регистре
            if task_id:
                document_category = 'task'
            elif route_id:
                document_category = 'route'
            else:
                # Проверяем валидность категории
                if document_category_str in ['personal', 'vehicle', 'task', 'route', 'other']:
                    document_category = document_category_str
                else:
                    # Default to personal if invalid category provided
                    document_category = 'personal'

            # Validate task access if task_id is provided
            if task_id:
                task = Task.query.get(task_id)
                if not task or task.assignee_id != current_user.id:
                    flash('You do not have access to upload documents to this task.', "danger")
                    return redirect(url_for('driver.documents'))

            # Validate route access if route_id is provided
            if route_id:
                route = Route.query.get(route_id)
                if not route or route.driver_id != current_user.driver.id:
                    flash('You do not have access to upload documents to this route.', "danger")
                    return redirect(url_for('driver.documents'))

            # Generate unique filename
            filename = secure_filename(document_file.filename)
            _, file_ext = os.path.splitext(filename)
            unique_filename = f"{current_user.id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{file_ext}"

            # Determine path based on document type
            if task_id:
                upload_path = os.path.join("documents", "tasks", str(task_id))
            elif route_id:
                upload_path = os.path.join("documents", "routes", str(route_id))
            else:
                upload_path = os.path.join("documents", "drivers", str(current_user.id), document_category)

            # Ensure directory exists
            full_path = os.path.join(current_app.config['UPLOAD_FOLDER'], upload_path)
            os.makedirs(full_path, exist_ok=True)

            # Save file
            file_path = os.path.join(full_path, unique_filename)
            document_file.save(file_path)

            # Calculate file size
            file_size = os.path.getsize(file_path)

            # Get file type
            file_type = file_ext.lstrip('.').lower()

            # Create document record
            document = Document(
                title=title,
                file_path=os.path.join(upload_path, unique_filename),
                file_type=file_type,
                size=file_size,
                uploaded_at=datetime.utcnow(),
                uploader_id=current_user.id,
                task_id=task_id,
                route_id=route_id,
                company_id=current_user.driver.company_id,
                document_category=DocumentCategory(document_category)
            )

            db.session.add(document)
            db.session.commit()

            log_action(ActionType.UPLOAD, f"Uploaded document: {title}", db)
            flash('Document uploaded successfully!', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'Error uploading document: {str(e)}', 'danger')

    return redirect(url_for('driver.documents', category=document_category))


@driver.route('/unread-messages')
@login_required
@role_required("DRIVER")
def unread_messages():
    """
    Show unread messages for driver
    """
    # Get unread messages count
    unread_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # If no unread messages, redirect to inbox
    if unread_count == 0:
        return redirect(url_for('messages.inbox'))

    # Get first unread message
    first_unread = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).order_by(Message.sent_at).first()

    # Redirect to chat with sender
    return redirect(url_for('messages.chat', user_id=first_unread.sender_id))


@driver.route('/tasks/<int:task_id>/start', methods=['POST'])
@login_required
@role_required("DRIVER")
def start_task(task_id):
    """
    Mark a task as started (in progress)
    """
    task = Task.query.get_or_404(task_id)

    # Check if task is assigned to this driver
    if task.assignee_id != current_user.id:
        flash('You are not assigned to this task.', "DANGER")
        return redirect(url_for('driver.tasks'))

    # Check if task is in NEW status
    if task.status != TaskStatus.NEW:
        flash('This task cannot be started as it is not in NEW status.', "DANGER")
        return redirect(url_for('driver.tasks'))

    try:
        # Update task status
        task.status = TaskStatus.IN_PROGRESS
        task.updated_at = datetime.utcnow()

        # Update related route if exists
        if task.route and task.route.status == RouteStatus.PLANNED:
            task.route.status = RouteStatus.IN_PROGRESS
            task.route.actual_start_time = datetime.utcnow()

        db.session.commit()
        log_action(ActionType.UPDATE, f"Started task {task.title}", db)
        flash('Task started successfully!', "SUCCESS")
    except Exception as e:
        db.session.rollback()
        flash(f'Error starting task: {str(e)}', "DANGER")

    return redirect(url_for('tasks.view_task', task_id=task_id))


@driver.route('/tasks/<int:task_id>/complete', methods=['POST'])
@login_required
@role_required("DRIVER")
def complete_task(task_id):
    """
    Mark a task as completed
    """
    task = Task.query.get_or_404(task_id)

    # Check if task is assigned to this driver
    if task.assignee_id != current_user.id:
        flash('You are not assigned to this task.', "DANGER")
        return redirect(url_for('driver.tasks'))

    # Check if task is in IN_PROGRESS status
    if task.status != TaskStatus.IN_PROGRESS:
        flash('This task cannot be completed as it is not in progress.', "DANGER")
        return redirect(url_for('driver.tasks'))

    try:
        # Update task status
        task.status = TaskStatus.COMPLETED
        task.updated_at = datetime.utcnow()

        # Update related route if exists
        if task.route and task.route.status == RouteStatus.IN_PROGRESS:
            task.route.status = RouteStatus.COMPLETED
            task.route.end_time = datetime.utcnow()

        db.session.commit()
        log_action(ActionType.UPDATE, f"Completed task {task.title}", db)
        flash('Task completed successfully!', "SUCCESS")
    except Exception as e:
        db.session.rollback()
        flash(f'Error completing task: {str(e)}', "DANGER")

    return redirect(url_for('tasks.view_task', task_id=task_id))


# Helper functions
def _get_driver_stats(driver_id):
    """
    Get statistics for a driver
    """
    # Get all routes
    routes = Route.query.filter_by(driver_id=driver_id).all()

    # Calculate stats
    total_routes = len(routes)
    completed_routes = sum(1 for r in routes if r.status == RouteStatus.COMPLETED)
    active_routes = sum(1 for r in routes if r.status == RouteStatus.IN_PROGRESS)
    planned_routes = sum(1 for r in routes if r.status == RouteStatus.PLANNED)

    # Calculate total distance
    total_distance = sum(r.distance or 0 for r in routes if r.status == RouteStatus.COMPLETED)

    # Calculate average completion time (in minutes)
    completion_times = []
    for route in routes:
        if route.status == RouteStatus.COMPLETED and route.start_time and route.end_time:
            duration = (route.end_time - route.start_time).total_seconds() / 60  # in minutes
            completion_times.append(duration)

    avg_completion_time = sum(completion_times) / len(completion_times) if completion_times else 0

    # Get on-time delivery rate
    on_time_deliveries = 0
    total_planned_deliveries = 0

    for route in routes:
        if route.status == RouteStatus.COMPLETED and route.end_time:
            if route.start_time:  # Only consider routes with planned start time
                total_planned_deliveries += 1
                # Add some buffer (e.g., 30 minutes)
                buffer = 30  # minutes
                planned_end_time = route.start_time
                if route.estimated_time:
                    # Add estimated time to planned start
                    from datetime import timedelta
                    planned_end_time = route.start_time + timedelta(minutes=route.estimated_time + buffer)

                if route.end_time <= planned_end_time:
                    on_time_deliveries += 1

    on_time_rate = (on_time_deliveries / total_planned_deliveries * 100) if total_planned_deliveries > 0 else 0

    # Get task counts for the driver
    active_tasks = Task.query.filter_by(
        assignee_id=driver_id,
        status=TaskStatus.IN_PROGRESS
    ).count()

    new_tasks = Task.query.filter_by(
        assignee_id=driver_id,
        status=TaskStatus.NEW
    ).count()

    completed_tasks = Task.query.filter_by(
        assignee_id=driver_id,
        status=TaskStatus.COMPLETED
    ).count()

    return {
        'total_routes': total_routes,
        'completed_routes': completed_routes,
        'active_routes': active_routes,
        'planned_routes': planned_routes,
        'total_distance': total_distance,
        'avg_completion_time': round(avg_completion_time, 1) if avg_completion_time else 0,
        'on_time_rate': round(on_time_rate, 1),
        'performance_score': min(round((completed_routes / total_routes * 100) if total_routes > 0 else 0, 1), 100),
        'active': active_tasks,
        'new': new_tasks,
        'completed_tasks': completed_tasks
    }


@driver.route('/routes')
@login_required
@role_required("DRIVER")
def driver_routes():
    """
    Show routes for the driver
    """
    # Check if user is a driver
    if not current_user.driver:
        flash('This page is only accessible to drivers.', "DANGER")
        return redirect(url_for('main.index'))

    # Get view parameter for filtering
    view = request.args.get("view", "all")

    # Get driver's routes statistics
    stats = _get_driver_stats(current_user.driver.id)

    # Get active route (in progress)
    active_route = Route.query.filter_by(
        driver_id=current_user.driver.id,
        status=RouteStatus.IN_PROGRESS
    ).first()

    # Get next route (planned)
    next_route = Route.query.filter_by(
        driver_id=current_user.driver.id,
        status=RouteStatus.PLANNED
    ).order_by(Route.start_time).first()

    # Get upcoming routes
    upcoming_routes = Route.query.filter_by(
        driver_id=current_user.driver.id,
        status=RouteStatus.PLANNED
    ).order_by(Route.start_time).offset(1).limit(5).all()

    # Get completed routes
    if view == "completed":
        # Show more completed routes when on the completed tab
        completed_routes = Route.query.filter_by(
            driver_id=current_user.driver.id,
            status=RouteStatus.COMPLETED
        ).order_by(Route.end_time.desc()).all()
    else:
        # Only show a few completed routes on other views
        completed_routes = Route.query.filter_by(
            driver_id=current_user.driver.id,
            status=RouteStatus.COMPLETED
        ).order_by(Route.end_time.desc()).limit(5).all()

    # If view is "active", hide completed routes
    if view == "active":
        completed_routes = []

    log_action(ActionType.VIEW, "Viewed driver routes", db)

    return render_template(
        'driver/routes.html',  # Use the new template
        title='My Routes',
        active_route=active_route,
        next_route=next_route,
        upcoming_routes=upcoming_routes,
        completed_routes=completed_routes,
        stats=stats,
        view=view  # Pass the view parameter to the template
    )