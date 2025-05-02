import traceback

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from datetime import datetime, timedelta

from flask_wtf import FlaskForm
from sqlalchemy import func, or_, and_
from wtforms import SelectField, StringField
from wtforms.validators import DataRequired, Length, Optional

from models import Message, ActionType

from app import db
from models import (
    User, UserRole, Manager, Operator, Driver, Company, CompanyOwner, Admin, Message,
    Task, TaskStatus, Route, RouteStatus, ActionType, Log
)
from utils import role_required, log_action
from forms import CompanyForm, UserForm, EditUserForm, OperatorForm, OperatorEditForm, DriverForm

owner = Blueprint('owner', __name__, url_prefix='/owner')


@owner.route('/dashboard/owner')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def dashboard():
    """
    Dashboard for company owner with analytics and stats
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id
    company = Company.query.get(company_id)

    # Get team statistics
    manager_count = Manager.query.filter_by(company_id=company_id).count()
    operator_count = Operator.query.filter_by(company_id=company_id).count()
    driver_count = Driver.query.filter_by(company_id=company_id).count()
    team_count = manager_count + operator_count + driver_count

    # Get task statistics
    task_query = Task.query.filter_by(company_id=company_id)
    task_count = task_query.count()
    completed_tasks = task_query.filter_by(status=TaskStatus.COMPLETED).count()
    in_progress_tasks = task_query.filter_by(status=TaskStatus.IN_PROGRESS).count()
    new_tasks = task_query.filter_by(status=TaskStatus.NEW).count()
    active_tasks = in_progress_tasks + new_tasks

    # Get route statistics
    route_query = Route.query.filter_by(company_id=company_id)
    route_count = route_query.count()
    completed_routes = route_query.filter_by(status=RouteStatus.COMPLETED).count()
    in_progress_routes = route_query.filter_by(status=RouteStatus.IN_PROGRESS).count()
    planned_routes = route_query.filter_by(status=RouteStatus.PLANNED).count()
    active_routes = in_progress_routes + planned_routes

    # Calculate efficiency score (simplified example)
    if task_count > 0:
        completion_rate = (completed_tasks / task_count) * 100
    else:
        completion_rate = 0

    if route_count > 0:
        route_completion_rate = (completed_routes / route_count) * 100
    else:
        route_completion_rate = 0

    # Simple efficiency calculation
    efficiency_score = int((completion_rate + route_completion_rate) / 2) if task_count > 0 and route_count > 0 else 0

    # Get recent logs
    recent_logs = Log.query.filter_by(company_id=company_id).order_by(Log.timestamp.desc()).limit(5).all()

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    log_action(ActionType.VIEW, "Viewed owner dashboard", db)

    return render_template(
        'owner/dashboard.html',
        title='Company Owner Dashboard',
        company=company,
        manager_count=manager_count,
        operator_count=operator_count,
        driver_count=driver_count,
        team_count=team_count,
        task_count=task_count,
        completed_tasks=completed_tasks,
        in_progress_tasks=in_progress_tasks,
        new_tasks=new_tasks,
        active_tasks=active_tasks,
        route_count=route_count,
        completed_routes=completed_routes,
        in_progress_routes=in_progress_routes,
        planned_routes=planned_routes,
        active_routes=active_routes,
        efficiency_score=efficiency_score,
        recent_logs=recent_logs,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/managers')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def managers():
    """
    Managers list for company owner
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id
    page = request.args.get("page", 1, type=int)

    # Get all managers for this company
    managers_query = Manager.query.filter_by(company_id=company_id)

    # Order by name
    managers_query = managers_query.join(User, Manager.id == User.id).order_by(User.first_name, User.last_name)

    # Paginate results
    managers = managers_query.paginate(page=page, per_page=10)

    # Calculate performance metrics for each manager
    for manager in managers.items:
        # Get tasks created by this manager
        tasks = Task.query.filter_by(creator_id=manager.id, company_id=company_id).all()
        total_tasks = len(tasks)

        # Calculate completed tasks
        completed_tasks = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)

        # Calculate active tasks (in progress and new)
        active_tasks = sum(1 for t in tasks if t.status in [TaskStatus.IN_PROGRESS, TaskStatus.NEW])

        # Calculate completion rate
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

        # Get operators managed by this manager
        operators_count = Operator.query.filter_by(manager_id=manager.id).count()

        # Get routes associated with drivers under this manager's operators
        operator_ids = [op.id for op in Operator.query.filter_by(manager_id=manager.id).all()]
        driver_ids = []

        if operator_ids:
            drivers = Driver.query.filter(Driver.operator_id.in_(operator_ids)).all()
            driver_ids = [d.id for d in drivers]

        routes = []
        if driver_ids:
            routes = Route.query.filter(Route.driver_id.in_(driver_ids)).all()

        # Calculate route metrics
        total_routes = len(routes)
        completed_routes = sum(1 for r in routes if r.status == RouteStatus.COMPLETED)

        # Calculate on-time delivery rate
        on_time_deliveries = 0
        total_completed_routes = 0

        for route in routes:
            if route.status == RouteStatus.COMPLETED and route.end_time and route.start_time:
                total_completed_routes += 1
                # Add 30 minutes buffer for on-time calculation
                from datetime import timedelta
                buffer = timedelta(minutes=30)

                if route.estimated_time:
                    planned_end_time = route.start_time + timedelta(minutes=route.estimated_time)
                    if route.end_time <= (planned_end_time + buffer):
                        on_time_deliveries += 1

        on_time_rate = (on_time_deliveries / total_completed_routes * 100) if total_completed_routes > 0 else 0

        # Calculate overall performance score (consider task completion and route metrics)
        performance_factors = []

        if total_tasks > 0:
            performance_factors.append(completion_rate)

        if total_completed_routes > 0:
            performance_factors.append(on_time_rate)

        # Add team size factor (larger team = potentially harder to manage)
        team_efficiency = min(operators_count * 5, 20) if operators_count > 0 else 0
        if team_efficiency > 0:
            performance_factors.append(team_efficiency)

        # Calculate overall score
        performance_score = int(sum(performance_factors) / len(performance_factors)) if performance_factors else 0

        # Attach metrics to manager object
        manager.active_tasks = active_tasks
        manager.completed_tasks = completed_tasks
        manager.total_tasks = total_tasks
        manager.completion_rate = round(completion_rate, 1)
        manager.performance_score = performance_score
        manager.on_time_rate = round(on_time_rate, 1)

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    log_action(ActionType.VIEW, "Viewed managers list", db)

    return render_template(
        'owner/managers.html',
        title='Company Managers',
        managers=managers,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/managers/add', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def add_manager():
    """
    Add a new manager
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Create form
    form = UserForm()
    form.role.data = UserRole.MANAGER.value

    if form.validate_on_submit():
        try:
            # Create user with manager role
            user = User(
                username=form.username.data,
                email=form.email.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                phone=form.phone.data,
                role=UserRole.MANAGER,
                is_active=form.is_active.data
            )

            # Set password
            user.set_password(form.password.data)

            # Handle profile image if provided
            if form.profile_image.data:
                from utils import save_profile_image
                user.profile_image = save_profile_image(form.profile_image.data)

            db.session.add(user)
            db.session.flush()  # Get user ID

            # Create manager relationship with company
            manager = Manager(id=user.id, company_id=company_id)
            db.session.add(manager)

            db.session.commit()
            log_action(ActionType.CREATE, f"Created manager {user.username}", db)

            flash(f'Manager {user.first_name} {user.last_name} created successfully!', "success")
            return redirect(url_for('owner.managers'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating manager: {str(e)}', "danger")

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    return render_template(
        'owner/add_manager.html',
        title='Add New Manager',
        form=form,
        UserRole=UserRole,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/managers/<int:manager_id>/view')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def view_manager(manager_id):
    """
    View manager details including their team and performance
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get manager
    manager = Manager.query.get_or_404(manager_id)

    # Ensure manager belongs to owner's company
    if manager.company_id != company_id:
        flash('You do not have permission to view this manager.', "danger")
        return redirect(url_for('owner.managers'))

    # Get manager's team (operators)
    operators = manager.operators

    # Count drivers under this manager's operators
    driver_count = Driver.query.join(Operator, Driver.operator_id == Operator.id) \
        .filter(Operator.manager_id == manager_id).count()

    # Calculate performance metrics
    # This would typically be calculated from task completion rates, etc.
    # For demo purposes, we'll use sample data
    tasks = Task.query.filter(
        Task.company_id == company_id,
        Task.creator_id == manager.id
    ).all()

    task_count = len(tasks)
    completed_tasks = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
    in_progress_tasks = sum(1 for t in tasks if t.status == TaskStatus.IN_PROGRESS)

    # Calculate completion rate
    completion_rate = f"{round((completed_tasks / task_count) * 100) if task_count > 0 else 0}%"

    # Get routes associated with this manager's team
    routes = Route.query.join(Driver, Route.driver_id == Driver.id) \
        .join(Operator, Driver.operator_id == Operator.id) \
        .filter(Operator.manager_id == manager_id).all()

    on_time_deliveries = 0
    total_routes = len(routes)

    for route in routes:
        if route.status == RouteStatus.COMPLETED and route.end_time and route.start_time:
            # Simplified on-time calculation (should include estimated time in real app)
            estimated_time = route.estimated_time or 120  # 2 hours default
            actual_time = (route.end_time - route.start_time).total_seconds() / 60
            if actual_time <= estimated_time * 1.1:  # 10% buffer
                on_time_deliveries += 1

    on_time_rate = f"{round((on_time_deliveries / total_routes) * 100) if total_routes > 0 else 0}%"

    # Get available operators for assignment
    available_operators = Operator.query.filter_by(company_id=company_id).all()

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    log_action(ActionType.VIEW, f"Viewed manager {manager.user.first_name} {manager.user.last_name}", db)

    return render_template(
        'owner/view_manager.html',
        title=f'Manager: {manager.user.first_name} {manager.user.last_name}',
        manager=manager,
        driver_count=driver_count,
        task_count=task_count,
        completed_tasks=completed_tasks,
        in_progress_tasks=in_progress_tasks,
        completion_rate=completion_rate,
        on_time_rate=on_time_rate,
        available_operators=available_operators,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/managers/<int:manager_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def edit_manager(manager_id):
    """
    Edit manager details
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get manager
    manager = Manager.query.get_or_404(manager_id)

    # Ensure manager belongs to owner's company
    if manager.company_id != company_id:
        flash('You do not have permission to edit this manager.', "DANGER")
        return redirect(url_for('owner.managers'))

    # Create form
    form = EditUserForm(
        original_username=manager.user.username,
        original_email=manager.user.email
    )

    if request.method == 'GET':
        form.username.data = manager.user.username
        form.email.data = manager.user.email
        form.first_name.data = manager.user.first_name
        form.last_name.data = manager.user.last_name
        form.phone.data = manager.user.phone
        form.is_active.data = manager.user.is_active

    if form.validate_on_submit():
        try:
            # Update user details
            manager.user.username = form.username.data
            manager.user.email = form.email.data
            manager.user.first_name = form.first_name.data
            manager.user.last_name = form.last_name.data
            manager.user.phone = form.phone.data
            manager.user.is_active = form.is_active.data

            # Handle profile image if provided
            if form.profile_image.data:
                from utils import save_profile_image
                manager.user.profile_image = save_profile_image(form.profile_image.data)

            db.session.commit()
            log_action(ActionType.UPDATE, f"Updated manager {manager.user.username}", db.session)

            flash(f'Manager {manager.user.first_name} {manager.user.last_name} updated successfully!', "SUCCESS")
            return redirect(url_for('main.view_manager', manager_id=manager_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating manager: {str(e)}', "DANGER")

    return render_template(
        'owner/edit_manager.html',
        title='Edit Manager',
        form=form,
        manager=manager
    )


@owner.route('/dashboard/owner/managers/<int:manager_id>/delete', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def delete_manager(manager_id):
    """
    Delete a manager
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get manager
    manager = Manager.query.get_or_404(manager_id)

    # Ensure manager belongs to owner's company
    if manager.company_id != company_id:
        flash('You do not have permission to delete this manager.', "DANGER")
        return redirect(url_for('owner.managers'))

    # Check if manager has operators
    if manager.operators:
        flash('Cannot delete manager with assigned operators. Please reassign operators first.', "DANGER")
        return redirect(url_for('main.view_manager', manager_id=manager_id))

    try:
        # Get user for log
        username = manager.user.username
        user_id = manager.user.id

        # Delete the manager (and user cascade)
        db.session.delete(manager.user)
        db.session.commit()

        log_action(ActionType.DELETE, f"Deleted manager {username}", db.session)
        flash(f'Manager {username} deleted successfully!', "SUCCESS")
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting manager: {str(e)}', "DANGER")

    return redirect(url_for('owner.managers'))


@owner.route('/dashboard/owner/managers/<int:manager_id>/assign-operators', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def assign_operators_to_manager(manager_id):
    """
    Assign operators to a manager
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get manager
    manager = Manager.query.get_or_404(manager_id)

    # Ensure manager belongs to owner's company
    if manager.company_id != company_id:
        flash('You do not have permission to modify this manager.', "DANGER")
        return redirect(url_for('owner.managers'))

    # Get selected operator IDs
    operator_ids = request.form.getlist('operator_ids[]')

    try:
        # Get all operators for this company
        company_operators = Operator.query.filter_by(company_id=company_id).all()

        # Update manager assignments
        for operator in company_operators:
            if str(operator.id) in operator_ids:
                # Assign to this manager
                operator.manager_id = manager_id
            elif operator.manager_id == manager_id:
                # Unassign from this manager
                operator.manager_id = None

        db.session.commit()
        log_action(ActionType.UPDATE, f"Updated operator assignments for manager {manager.user.username}", db.session)

        flash('Operator assignments updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating operator assignments: {str(e)}', 'danger')

    return redirect(url_for('owner.view_manager', manager_id=manager_id))

@owner.route('/dashboard/owner/operators/<int:operator_id>/remove-from-manager/<int:manager_id>', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def remove_operator_from_manager(operator_id, manager_id):
    """
    Remove an operator from a manager
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get operator and manager
    operator = Operator.query.get_or_404(operator_id)
    manager = Manager.query.get_or_404(manager_id)

    # Ensure they belong to owner's company
    if operator.company_id != company_id or manager.company_id != company_id:
        flash('You do not have permission to modify these users.', "DANGER")
        return redirect(url_for('owner.managers'))

    # Ensure operator is assigned to this manager
    if operator.manager_id != manager_id:
        flash('This operator is not assigned to the specified manager.', "WARNING")
        return redirect(url_for('owner.view_manager', manager_id=manager_id))

    try:
        # Remove manager assignment
        operator.manager_id = None
        db.session.commit()

        log_action(ActionType.UPDATE, f"Removed operator {operator.user.username} from manager {manager.user.username}",
                   db)
        flash(f'Operator {operator.user.first_name} {operator.user.last_name} removed from manager successfully!',
              "SUCCESS")
    except Exception as e:
        db.session.rollback()
        flash(f'Error removing operator from manager: {str(e)}', "DANGER")

    return redirect(url_for('owner.view_manager', manager_id=manager_id))


@owner.route('/dashboard/owner/operator-requests')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def operator_requests():
    """
    Show operator requests for the company owner
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id
    page = request.args.get("page", 1, type=int)

    # Get messages that are operator requests
    # The requests contain 'requests a new operator account' in the content
    request_messages = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%')
    ).order_by(Message.sent_at.desc())

    # Paginate the results
    requests = request_messages.paginate(page=page, per_page=10)

    # Get count of all unread messages
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Mark these messages as read when viewing
    for message in requests.items:
        if not message.is_read:
            message.is_read = True

    db.session.commit()
    log_action(ActionType.VIEW, "Viewed operator requests", db)

    return render_template(
        'owner/operator_requests.html',
        title='Operator Requests',
        requests=requests,
        unread_messages_count=unread_messages_count
    )


@owner.route('/dashboard/owner/operator-requests/<int:message_id>/approve', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def approve_operator_request(message_id):
    """
    Approve an operator request and redirect to create operator form
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    # Get the request message
    message = Message.query.get_or_404(message_id)

    # Check if this is the recipient
    if message.recipient_id != current_user.id:
        flash('You do not have permission to approve this request.', "danger")
        return redirect(url_for('owner.operator_requests'))

    # Extract manager ID (sender)
    manager_id = message.sender_id

    # Extract request details from message content
    lines = message.content.split('\n')
    name_line = next((line for line in lines if line.startswith('Name:')), None)
    email_line = next((line for line in lines if line.startswith('Email:')), None)
    phone_line = next((line for line in lines if line.startswith('Phone:')), None)

    # Extract manager ID from the message content
    manager_id_line = next((line for line in lines if line.startswith('Manager ID:')), None)
    if manager_id_line:
        # Extract manager ID from the line
        try:
            extracted_manager_id = int(manager_id_line.replace('Manager ID:', '').strip())
            # Verify that this manager exists
            manager = Manager.query.get(extracted_manager_id)
            if manager:
                manager_id = extracted_manager_id
        except (ValueError, TypeError):
            # If extraction fails, use the sender_id as fallback
            pass

    if name_line and email_line:
        # Extract name, email and phone
        name_parts = name_line.replace('Name:', '').strip().split()
        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ""
        email = email_line.replace('Email:', '').strip()
        phone = phone_line.replace('Phone:', '').strip() if phone_line else ""
        phone = phone if phone != 'Not provided' else ""

        # Mark message as read
        message.is_read = True
        db.session.commit()

        log_action(ActionType.UPDATE, f"Approved operator request from {message.sender.username}", db)

        # Flash success message
        flash('Request approved. Please complete the operator creation form.', "success")

        # Redirect to owner's add_operator form with pre-filled data
        return redirect(url_for(
            'owner.add_operator',
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            manager_id=manager_id
        ))
    else:
        flash('Could not extract information from the request. Please create operator manually.', "warning")
        return redirect(url_for('owner.add_operator'))


@owner.route('/dashboard/owner/operator-requests/<int:message_id>/reject', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def reject_operator_request(message_id):
    """
    Reject an operator request and notify the manager
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    # Get the request message
    message = Message.query.get_or_404(message_id)

    # Check if this is the recipient
    if message.recipient_id != current_user.id:
        flash('You do not have permission to reject this request.', "danger")
        return redirect(url_for('owner.operator_requests'))

    company_id = current_user.company_owner.company_id
    reason = request.form.get('rejection_reason', 'No reason provided')

    try:
        # Mark original message as read
        message.is_read = True

        # Send rejection message to the manager
        rejection_message = Message(
            content=f"Your request for a new operator has been rejected.\n\nReason: {reason}",
            sender_id=current_user.id,
            recipient_id=message.sender_id,
            company_id=company_id,
            is_read=False,
            sent_at=datetime.utcnow()
        )

        db.session.add(rejection_message)
        db.session.commit()

        log_action(ActionType.UPDATE, f"Rejected operator request from {message.sender.username}", db)
        flash('Request rejected and manager notified.', "success")
    except Exception as e:
        db.session.rollback()
        flash(f'Error rejecting request: {str(e)}', "danger")

    return redirect(url_for('owner.operator_requests'))


@owner.route('/dashboard/owner/company-settings')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def company_settings():
    """
    Company settings page for owner
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id
    company = Company.query.get(company_id)

    # Get team statistics
    manager_count = Manager.query.filter_by(company_id=company_id).count()
    operator_count = Operator.query.filter_by(company_id=company_id).count()
    driver_count = Driver.query.filter_by(company_id=company_id).count()
    total_team_count = manager_count + operator_count + driver_count

    # Get mock company settings
    # In a real app, this would be from a settings table
    company_settings = {
        "ENABLE_NOTIFICATIONS": True,
        "TASK_ASSIGNMENT_APPROVAL": False,
        "ALLOW_DRIVER_MESSAGING": True,
        "AUTO_ROUTE_OPTIMIZATION": False,
        "DEFAULT_TASK_DEADLINE": 24
    }

    log_action(ActionType.VIEW, "Viewed company settings", db.session)

    return render_template(
        'owner/company_settings.html',
        title='Company Settings',
        company=company,
        manager_count=manager_count,
        operator_count=operator_count,
        driver_count=driver_count,
        total_team_count=total_team_count,
        company_settings=company_settings
    )


@owner.route('/dashboard/owner/edit-company', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def edit_company():
    """
    Edit company details
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id
    company = Company.query.get(company_id)

    # Create form
    form = CompanyForm()

    if request.method == 'GET':
        form.name.data = company.name
        form.legal_name.data = company.legal_name
        form.tax_id.data = company.tax_id
        form.address.data = company.address
        form.phone.data = company.phone
        form.email.data = company.email
        form.website.data = company.website

    if form.validate_on_submit():
        try:
            # Update company details
            company.name = form.name.data
            company.legal_name = form.legal_name.data
            company.tax_id = form.tax_id.data
            company.address = form.address.data
            company.phone = form.phone.data
            company.email = form.email.data
            company.website = form.website.data

            db.session.commit()
            log_action(ActionType.UPDATE, f"Updated company details for {company.name}", db.session)

            flash('Company details updated successfully!', "SUCCESS")
            return redirect(url_for('owner.company_settings'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating company details: {str(e)}', "DANGER")

    return render_template(
        'owner/edit_company.html',
        title='Edit Company',
        form=form,
        company=company
    )


@owner.route('/dashboard/owner/update-company-settings', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def update_company_settings():
    """
    Update company settings
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    # In a real app, this would update a settings table
    # For this demo, we'll just acknowledge the update

    enable_notifications = "ENABLE_NOTIFICATIONS" in request.form
    task_assignment_approval = "TASK_ASSIGNMENT_APPROVAL" in request.form
    allow_driver_messaging = "ALLOW_DRIVER_MESSAGING" in request.form
    auto_route_optimization = "AUTO_ROUTE_OPTIMIZATION" in request.form
    default_task_deadline = int(request.form.get("DEFAULT_TASK_DEADLINE", 24))

    # Log the settings update
    settings_summary = f"Notifications: {enable_notifications}, " \
                       f"Task Approval: {task_assignment_approval}, " \
                       f"Driver Messaging: {allow_driver_messaging}, " \
                       f"Route Optimization: {auto_route_optimization}, " \
                       f"Default Deadline: {default_task_deadline}h"

    log_action(ActionType.UPDATE, f"Updated company settings: {settings_summary}", db.session)

    flash('Company settings updated successfully!', "SUCCESS")
    return redirect(url_for('owner.company_settings'))


@owner.route('/operators/<int:operator_id>')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def view_operator(operator_id):
    """
    View details of a specific operator for company owner
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get operator
    operator = Operator.query.get_or_404(operator_id)

    # Check if the operator belongs to this company
    if operator.company_id != company_id:
        flash('This operator is not part of your company.', "danger")
        return redirect(url_for('owner.dashboard'))

    # Get activity logs for this operator
    activity_logs = Log.query.filter_by(
        user_id=operator.id
    ).order_by(Log.timestamp.desc()).limit(10).all()

    # Get drivers managed by this operator
    drivers = Driver.query.filter_by(operator_id=operator.id).all()

    # Get manager for this operator
    manager = None
    if operator.manager_id:
        manager = Manager.query.get(operator.manager_id)

    # Get tasks created by this operator
    tasks = Task.query.filter_by(
        creator_id=operator.id
    ).order_by(Task.created_at.desc()).limit(5).all()

    # Get performance metrics
    task_count = Task.query.filter_by(creator_id=operator.id).count()
    completed_tasks = Task.query.filter_by(
        creator_id=operator.id, status=TaskStatus.COMPLETED
    ).count()
    completion_rate = (completed_tasks / task_count * 100) if task_count > 0 else 0

    # Get route metrics
    routes_query = Route.query.join(
        Driver, Route.driver_id == Driver.id
    ).filter(
        Driver.operator_id == operator.id
    )
    total_routes = routes_query.count()
    completed_routes = routes_query.filter(
        Route.status == RouteStatus.COMPLETED
    ).count()

    # Calculate route completion rate
    route_completion_rate = (completed_routes / total_routes * 100) if total_routes > 0 else 0

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    log_action(ActionType.VIEW, f"Viewed operator {operator.user.username}", db)

    return render_template(
        'owner/view_operator.html',
        title=f'Operator: {operator.user.first_name} {operator.user.last_name}',
        operator=operator,
        activity_logs=activity_logs,
        drivers=drivers,
        manager=manager,
        tasks=tasks,
        task_count=task_count,
        completed_tasks=completed_tasks,
        completion_rate=round(completion_rate, 1),
        total_routes=total_routes,
        completed_routes=completed_routes,
        route_completion_rate=round(route_completion_rate, 1),
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )

@owner.route('/dashboard/owner/operators')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def operators():
    """
    Operators list for company owner
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id
    page = request.args.get("page", 1, type=int)

    # Get all operators for this company
    operators_query = Operator.query.filter_by(company_id=company_id)

    # Order by name
    operators_query = operators_query.join(User, Operator.id == User.id).order_by(User.first_name, User.last_name)

    # Paginate results
    operators = operators_query.paginate(page=page, per_page=10)

    # Calculate performance metrics for each operator
    for operator in operators.items:
        # Get tasks created by this operator
        tasks = Task.query.filter_by(creator_id=operator.id, company_id=company_id).all()
        total_tasks = len(tasks)

        # Calculate completed tasks
        completed_tasks = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)

        # Calculate active tasks (in progress and new)
        active_tasks = sum(1 for t in tasks if t.status in [TaskStatus.IN_PROGRESS, TaskStatus.NEW])

        # Calculate completion rate
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

        # Get drivers managed by this operator
        drivers_count = Driver.query.filter_by(operator_id=operator.id).count()

        # Calculate routes under operator's drivers
        driver_ids = [d.id for d in Driver.query.filter_by(operator_id=operator.id).all()]
        routes = []
        if driver_ids:
            routes = Route.query.filter(Route.driver_id.in_(driver_ids)).all()

        # Calculate route metrics
        total_routes = len(routes)
        completed_routes = sum(1 for r in routes if r.status == RouteStatus.COMPLETED)

        # Calculate on-time delivery rate
        on_time_deliveries = 0
        total_completed_routes = 0

        for route in routes:
            if route.status == RouteStatus.COMPLETED and route.end_time and route.start_time:
                total_completed_routes += 1
                # Add 30 minutes buffer for on-time calculation
                from datetime import timedelta
                buffer = timedelta(minutes=30)

                if route.estimated_time:
                    planned_end_time = route.start_time + timedelta(minutes=route.estimated_time)
                    if route.end_time <= (planned_end_time + buffer):
                        on_time_deliveries += 1

        on_time_rate = (on_time_deliveries / total_completed_routes * 100) if total_completed_routes > 0 else 0

        # Calculate overall performance score
        performance_factors = []

        if total_tasks > 0:
            performance_factors.append(completion_rate)

        if total_completed_routes > 0:
            performance_factors.append(on_time_rate)

        # Add team size factor
        team_efficiency = min(drivers_count * 5, 20) if drivers_count > 0 else 0
        if team_efficiency > 0:
            performance_factors.append(team_efficiency)

        # Calculate overall score
        performance_score = int(sum(performance_factors) / len(performance_factors)) if performance_factors else 0

        # Attach metrics to operator object
        operator.active_tasks = active_tasks
        operator.completed_tasks = completed_tasks
        operator.total_tasks = total_tasks
        operator.completion_rate = round(completion_rate, 1)
        operator.performance_score = performance_score
        operator.on_time_rate = round(on_time_rate, 1)

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    log_action(ActionType.VIEW, "Viewed operators list", db)

    return render_template(
        'owner/operators.html',
        title='Company Operators',
        operators=operators,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/operators/add', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def add_operator():
    """
    Add a new operator
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Create form
    form = OperatorForm()

    # Get available managers for this company
    managers = Manager.query.filter_by(company_id=company_id).all()
    manager_choices = [(m.id, f"{m.user.first_name} {m.user.last_name}") for m in managers]
    manager_choices.insert(0, (0, "-- Select Manager --"))

    # Set manager choices in the form
    form.manager_id.choices = manager_choices

    # Prefill form from request parameters if provided (GET request or form validation fails)
    if request.method == 'GET' or not form.validate():
        # Get parameters from request
        first_name = request.args.get('first_name', '')
        last_name = request.args.get('last_name', '')
        email = request.args.get('email', '')
        phone = request.args.get('phone', '')
        manager_id = request.args.get('manager_id', 0)

        # Only prefill if the form fields are empty (to preserve user edits)
        if not form.first_name.data:
            form.first_name.data = first_name
        if not form.last_name.data:
            form.last_name.data = last_name
        if not form.email.data:
            form.email.data = email
        if not form.phone.data:
            form.phone.data = phone
        if not form.manager_id.data and manager_id:
            # Verify manager_id is valid
            try:
                manager_id = int(manager_id)
                # Check if this manager ID is in our choices
                if any(manager_id == choice[0] for choice in manager_choices):
                    form.manager_id.data = manager_id
            except (ValueError, TypeError):
                pass

        # Generate a suggested username based on first and last name if provided
        if first_name and last_name and not form.username.data:
            # Create username like john.doe or john.doe1 if john.doe exists
            import re
            # Convert to lowercase and replace spaces/special chars
            base_username = f"{first_name.lower()}.{last_name.lower()}"
            base_username = re.sub(r'[^a-z.]', '', base_username)

            # Check if username exists
            if base_username:
                username = base_username
                counter = 1
                while User.query.filter_by(username=username).first():
                    username = f"{base_username}{counter}"
                    counter += 1
                form.username.data = username

    if form.validate_on_submit():
        try:
            # Create user with operator role
            user = User(
                username=form.username.data,
                email=form.email.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                phone=form.phone.data,
                role=UserRole.OPERATOR,
                is_active=form.is_active.data
            )

            # Set password
            user.set_password(form.password.data)

            # Handle profile image if provided
            if form.profile_image.data:
                from utils import save_profile_image
                user.profile_image = save_profile_image(form.profile_image.data)

            db.session.add(user)
            db.session.flush()  # Get user ID

            # Create operator relationship with company and manager
            manager_id = form.manager_id.data if form.manager_id.data != 0 else None
            operator = Operator(id=user.id, company_id=company_id, manager_id=manager_id)
            db.session.add(operator)

            db.session.commit()
            log_action(ActionType.CREATE, f"Created operator {user.username}", db)

            flash(f'Operator {user.first_name} {user.last_name} created successfully!', "success")
            return redirect(url_for('owner.operators'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating operator: {str(e)}', "danger")

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    return render_template(
        'owner/add_operator.html',
        title='Add New Operator',
        form=form,
        UserRole=UserRole,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/operators/<int:operator_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def edit_operator(operator_id):
    """
    Edit operator details
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get operator
    operator = Operator.query.get_or_404(operator_id)

    # Ensure operator belongs to owner's company
    if operator.company_id != company_id:
        flash('You do not have permission to edit this operator.', "danger")
        return redirect(url_for('owner.operators'))

    # Get available managers for this company
    managers = Manager.query.filter_by(company_id=company_id).all()
    manager_choices = [(str(m.id), f"{m.user.first_name} {m.user.last_name}") for m in managers]
    manager_choices.insert(0, ("0", "-- Select Manager --"))

    # Create form (exclude validators for role field)
    form = EditUserForm(
        original_username=operator.user.username,
        original_email=operator.user.email
    )

    # Remove role field from form validation by setting it to not required
    # And populate its choices with the fixed value
    if hasattr(form, 'role'):
        form.role.validators = []
        form.role.choices = [(UserRole.OPERATOR.value, 'Operator')]
        form.role.data = UserRole.OPERATOR.value

    # Populate form with current values on GET
    if request.method == 'GET':
        form.username.data = operator.user.username
        form.email.data = operator.user.email
        form.first_name.data = operator.user.first_name
        form.last_name.data = operator.user.last_name
        form.phone.data = operator.user.phone
        form.is_active.data = operator.user.is_active

    # Process form submission
    if request.method == 'POST':
        current_app.logger.info(f"Form submitted: {request.form}")
        current_app.logger.info(f"manager_id from form: {request.form.get('manager_id')}")

        # Force role to be valid to avoid validation errors
        if hasattr(form, 'role'):
            form.role.data = UserRole.OPERATOR.value

        if form.validate_on_submit():
            try:
                # Update user fields from form
                operator.user.username = form.username.data
                operator.user.email = form.email.data
                operator.user.first_name = form.first_name.data
                operator.user.last_name = form.last_name.data
                operator.user.phone = form.phone.data
                operator.user.is_active = form.is_active.data

                # Handle manager_id separately
                manager_id_str = request.form.get('manager_id', '0')
                current_app.logger.info(f"Processing manager_id: {manager_id_str}")

                if manager_id_str == '0':
                    operator.manager_id = None
                    current_app.logger.info("Setting manager_id to None")
                else:
                    try:
                        manager_id = int(manager_id_str)
                        # Verify that this manager exists and belongs to the same company
                        manager = Manager.query.filter_by(id=manager_id, company_id=company_id).first()
                        if manager:
                            operator.manager_id = manager_id
                            current_app.logger.info(f"Setting manager_id to {manager_id}")
                        else:
                            operator.manager_id = None
                            current_app.logger.warning(f"Manager {manager_id} not found or not in same company")
                    except (ValueError, TypeError) as e:
                        operator.manager_id = None
                        current_app.logger.error(f"Error converting manager_id: {str(e)}")

                # Handle profile image if provided
                if form.profile_image.data:
                    current_app.logger.info("Processing profile image")
                    from utils import save_profile_image
                    profile_image = save_profile_image(form.profile_image.data)
                    if profile_image:
                        operator.user.profile_image = profile_image
                        current_app.logger.info(f"Profile image saved: {profile_image}")
                    else:
                        current_app.logger.warning("Failed to save profile image")

                # Save changes
                current_app.logger.info("Committing changes to database")
                db.session.commit()
                log_action(ActionType.UPDATE, f"Updated operator {operator.user.username}", db)

                flash(f'Operator {operator.user.first_name} {operator.user.last_name} updated successfully!', "success")
                return redirect(url_for('owner.view_operator', operator_id=operator_id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating operator: {str(e)}")
                current_app.logger.error(traceback.format_exc())
                flash(f'Error updating operator: {str(e)}', "danger")
        else:
            current_app.logger.error(f"Form validation errors: {form.errors}")
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"{field}: {error}", "danger")

    # Get unread messages count for UI
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count for UI
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    # Current manager_id for the template
    current_manager_id = str(operator.manager_id) if operator.manager_id else "0"
    current_app.logger.info(f"Rendering template with current_manager_id: {current_manager_id}")

    return render_template(
        'owner/edit_operator.html',
        title='Edit Operator',
        form=form,
        operator=operator,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count,
        manager_choices=manager_choices,
        current_manager_id=current_manager_id
    )

@owner.route('/dashboard/owner/operators/<int:operator_id>/delete', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def delete_operator(operator_id):
    """
    Delete an operator
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get operator
    operator = Operator.query.get_or_404(operator_id)

    # Ensure operator belongs to owner's company
    if operator.company_id != company_id:
        flash('You do not have permission to delete this operator.', "danger")
        return redirect(url_for('owner.operators'))

    # Check if operator has drivers
    if operator.drivers:
        flash('Cannot delete operator with assigned drivers. Please reassign drivers first.', "danger")
        return redirect(url_for('owner.view_operator', operator_id=operator_id))

    try:
        # Get user for log
        username = operator.user.username
        user_id = operator.user.id

        # Delete the operator (and user cascade)
        db.session.delete(operator.user)
        db.session.commit()

        log_action(ActionType.DELETE, f"Deleted operator {username}", db)
        flash(f'Operator {username} deleted successfully!', "success")
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting operator: {str(e)}', "danger")

    return redirect(url_for('owner.operators'))


@owner.route('/dashboard/owner/drivers')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def drivers():
    """
    Drivers list for company owner
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id
    page = request.args.get("page", 1, type=int)

    # Get all drivers for this company
    drivers_query = Driver.query.filter_by(company_id=company_id)

    # Order by name
    drivers_query = drivers_query.join(User, Driver.id == User.id).order_by(User.first_name, User.last_name)

    # Paginate results
    drivers = drivers_query.paginate(page=page, per_page=10)

    # Calculate performance metrics for each driver
    for driver in drivers.items:
        # Get routes assigned to this driver
        routes = Route.query.filter_by(driver_id=driver.id, company_id=company_id).all()
        total_routes = len(routes)

        # Calculate completed routes
        completed_routes = sum(1 for r in routes if r.status == RouteStatus.COMPLETED)

        # Calculate active routes (in progress and planned)
        active_routes = sum(1 for r in routes if r.status in [RouteStatus.IN_PROGRESS, RouteStatus.PLANNED])

        # Calculate completion rate
        completion_rate = (completed_routes / total_routes * 100) if total_routes > 0 else 0

        # Calculate on-time delivery rate
        on_time_deliveries = 0
        total_completed_routes = 0

        for route in routes:
            if route.status == RouteStatus.COMPLETED and route.end_time and route.start_time:
                total_completed_routes += 1
                # Add 30 minutes buffer for on-time calculation
                from datetime import timedelta
                buffer = timedelta(minutes=30)

                if route.estimated_time:
                    planned_end_time = route.start_time + timedelta(minutes=route.estimated_time)
                    if route.end_time <= (planned_end_time + buffer):
                        on_time_deliveries += 1

        on_time_rate = (on_time_deliveries / total_completed_routes * 100) if total_completed_routes > 0 else 0

        # Calculate overall performance score
        performance_factors = []

        if total_routes > 0:
            performance_factors.append(completion_rate)

        if total_completed_routes > 0:
            performance_factors.append(on_time_rate)

        # Calculate overall score
        performance_score = int(sum(performance_factors) / len(performance_factors)) if performance_factors else 0

        # Attach metrics to driver object
        driver.active_routes = active_routes
        driver.completed_routes = completed_routes
        driver.total_routes = total_routes
        driver.completion_rate = round(completion_rate, 1)
        driver.performance_score = performance_score
        driver.on_time_rate = round(on_time_rate, 1)

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    log_action(ActionType.VIEW, "Viewed drivers list", db)

    return render_template(
        'owner/drivers.html',
        title='Company Drivers',
        drivers=drivers,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/drivers/add', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def add_driver():
    """
    Add a new driver
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Create form
    form = DriverForm()

    # Get available operators for this company
    operators = Operator.query.filter_by(company_id=company_id).all()
    operator_choices = [(o.id, f"{o.user.first_name} {o.user.last_name}") for o in operators]
    operator_choices.insert(0, (0, "-- Select Operator --"))

    # Set operator choices in the form
    form.operator_id.choices = operator_choices

    if form.validate_on_submit():
        try:
            # Create user with driver role
            user = User(
                username=form.username.data,
                email=form.email.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                phone=form.phone.data,
                role=UserRole.DRIVER,
                is_active=form.is_active.data
            )

            # Set password
            user.set_password(form.password.data)

            # Handle profile image if provided
            if form.profile_image.data:
                from utils import save_profile_image
                user.profile_image = save_profile_image(form.profile_image.data)

            db.session.add(user)
            db.session.flush()  # Get user ID

            # Create driver relationship with company and operator
            operator_id = form.operator_id.data if form.operator_id.data != 0 else None
            driver = Driver(
                id=user.id,
                company_id=company_id,
                operator_id=operator_id,
                license_number=form.license_number.data,
                vehicle_info=form.vehicle_info.data
            )
            db.session.add(driver)

            db.session.commit()
            log_action(ActionType.CREATE, f"Created driver {user.username}", db)

            flash(f'Driver {user.first_name} {user.last_name} created successfully!', "success")
            return redirect(url_for('owner.drivers'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating driver: {str(e)}', "danger")

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    return render_template(
        'owner/add_driver.html',
        title='Add New Driver',
        form=form,
        UserRole=UserRole,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/drivers/<int:driver_id>/view')
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def view_driver(driver_id):
    """
    View details of a specific driver for company owner
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get driver
    driver = Driver.query.get_or_404(driver_id)

    # Check if the driver belongs to this company
    if driver.company_id != company_id:
        flash('This driver is not part of your company.', "danger")
        return redirect(url_for('owner.dashboard'))

    # Get activity logs for this driver
    activity_logs = Log.query.filter_by(
        user_id=driver.id
    ).order_by(Log.timestamp.desc()).limit(10).all()

    # Get operator for this driver
    operator = None
    if driver.operator_id:
        operator = Operator.query.get(driver.operator_id)

    # Get routes assigned to this driver - используем start_time вместо created_at
    routes = Route.query.filter_by(
        driver_id=driver.id
    ).order_by(Route.start_time.desc()).limit(5).all()

    # Get performance metrics
    route_count = Route.query.filter_by(driver_id=driver.id).count()
    completed_routes = Route.query.filter_by(
        driver_id=driver.id, status=RouteStatus.COMPLETED
    ).count()
    completion_rate = (completed_routes / route_count * 100) if route_count > 0 else 0

    # Get active routes count for warning in deactivation modal
    active_routes = Route.query.filter(
        Route.driver_id == driver_id,
        Route.status.in_([RouteStatus.PLANNED, RouteStatus.IN_PROGRESS])
    ).count()

    # Calculate on-time rate
    on_time_deliveries = 0
    total_completed_routes = 0

    for route in Route.query.filter_by(driver_id=driver.id, status=RouteStatus.COMPLETED).all():
        if route.end_time and route.start_time:
            total_completed_routes += 1
            from datetime import timedelta
            buffer = timedelta(minutes=30)

            if route.estimated_time:
                planned_end_time = route.start_time + timedelta(minutes=route.estimated_time)
                if route.end_time <= (planned_end_time + buffer):
                    on_time_deliveries += 1

    on_time_rate = (on_time_deliveries / total_completed_routes * 100) if total_completed_routes > 0 else 0

    # Get average route duration for completed routes (in hours)
    avg_duration = 0
    total_duration_minutes = 0

    for route in Route.query.filter_by(driver_id=driver.id, status=RouteStatus.COMPLETED).all():
        if route.end_time and route.start_time:
            duration = (route.end_time - route.start_time).total_seconds() / 60  # in minutes
            total_duration_minutes += duration

    if total_completed_routes > 0:
        avg_duration = round(total_duration_minutes / total_completed_routes / 60, 1)  # в часах

    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    log_action(ActionType.VIEW, f"Viewed driver {driver.user.username}", db)

    return render_template(
        'owner/view_driver.html',
        title=f'Driver: {driver.user.first_name} {driver.user.last_name}',
        driver=driver,
        activity_logs=activity_logs,
        operator=operator,
        routes=routes,
        route_count=route_count,
        completed_routes=completed_routes,
        completion_rate=round(completion_rate, 1),
        on_time_rate=round(on_time_rate, 1),
        avg_duration=avg_duration,
        active_routes=active_routes,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count
    )


@owner.route('/dashboard/owner/drivers/<int:driver_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def edit_driver(driver_id):
    """
    Edit driver details
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get driver
    driver = Driver.query.get_or_404(driver_id)

    # Ensure driver belongs to owner's company
    if driver.company_id != company_id:
        flash('You do not have permission to edit this driver.', "danger")
        return redirect(url_for('owner.drivers'))

    # Get available operators for this company
    operators = Operator.query.filter_by(company_id=company_id).all()
    operator_choices = [(str(o.id), f"{o.user.first_name} {o.user.last_name}") for o in operators]
    operator_choices.insert(0, ("0", "-- Select Operator --"))

    # Create form (exclude validators for role field)
    form = EditUserForm(
        original_username=driver.user.username,
        original_email=driver.user.email
    )

    # Create form fields for license and vehicle info
    class F(FlaskForm):
        license_number = StringField('License Number', validators=[DataRequired(), Length(max=64)])
        vehicle_info = StringField('Vehicle Information', validators=[DataRequired(), Length(max=256)])
        operator_id = SelectField('Operator', coerce=str, validators=[Optional()], choices=operator_choices)

    for field in F():
        setattr(form, field.name, field)

    # Remove role field from form validation by setting it to not required
    # And populate its choices with the fixed value
    if hasattr(form, 'role'):
        form.role.validators = []
        form.role.choices = [(UserRole.DRIVER.value, 'Driver')]
        form.role.data = UserRole.DRIVER.value

    # Populate form with current values on GET
    if request.method == 'GET':
        form.username.data = driver.user.username
        form.email.data = driver.user.email
        form.first_name.data = driver.user.first_name
        form.last_name.data = driver.user.last_name
        form.phone.data = driver.user.phone
        form.is_active.data = driver.user.is_active
        form.license_number.data = driver.license_number
        form.vehicle_info.data = driver.vehicle_info

    # Process form submission
    if request.method == 'POST':
        current_app.logger.info(f"Form submitted: {request.form}")
        current_app.logger.info(f"operator_id from form: {request.form.get('operator_id')}")

        # Force role to be valid to avoid validation errors
        if hasattr(form, 'role'):
            form.role.data = UserRole.DRIVER.value

        if form.validate_on_submit():
            try:
                # Update user fields from form
                driver.user.username = form.username.data
                driver.user.email = form.email.data
                driver.user.first_name = form.first_name.data
                driver.user.last_name = form.last_name.data
                driver.user.phone = form.phone.data
                driver.user.is_active = form.is_active.data

                # Update driver-specific fields
                driver.license_number = form.license_number.data
                driver.vehicle_info = form.vehicle_info.data

                # Handle operator_id separately
                operator_id_str = request.form.get('operator_id', '0')
                current_app.logger.info(f"Processing operator_id: {operator_id_str}")

                if operator_id_str == '0':
                    driver.operator_id = None
                    current_app.logger.info("Setting operator_id to None")
                else:
                    try:
                        operator_id = int(operator_id_str)
                        # Verify that this operator exists and belongs to the same company
                        operator = Operator.query.filter_by(id=operator_id, company_id=company_id).first()
                        if operator:
                            driver.operator_id = operator_id
                            current_app.logger.info(f"Setting operator_id to {operator_id}")
                        else:
                            driver.operator_id = None
                            current_app.logger.warning(f"Operator {operator_id} not found or not in same company")
                    except (ValueError, TypeError) as e:
                        driver.operator_id = None
                        current_app.logger.error(f"Error converting operator_id: {str(e)}")

                # Handle profile image if provided
                if form.profile_image.data:
                    current_app.logger.info("Processing profile image")
                    from utils import save_profile_image
                    profile_image = save_profile_image(form.profile_image.data)
                    if profile_image:
                        driver.user.profile_image = profile_image
                        current_app.logger.info(f"Profile image saved: {profile_image}")
                    else:
                        current_app.logger.warning("Failed to save profile image")

                # Save changes
                current_app.logger.info("Committing changes to database")
                db.session.commit()
                log_action(ActionType.UPDATE, f"Updated driver {driver.user.username}", db)

                flash(f'Driver {driver.user.first_name} {driver.user.last_name} updated successfully!', "success")
                return redirect(url_for('owner.view_driver', driver_id=driver_id))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Error updating driver: {str(e)}")
                current_app.logger.error(traceback.format_exc())
                flash(f'Error updating driver: {str(e)}', "danger")
        else:
            current_app.logger.error(f"Form validation errors: {form.errors}")
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f"{field}: {error}", "danger")

    # Get unread messages count for UI
    unread_messages_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator request count for UI
    operator_requests_count = Message.query.filter(
        Message.recipient_id == current_user.id,
        Message.company_id == company_id,
        Message.content.like('%requests a new operator account%'),
        Message.is_read == False
    ).count()

    # Current operator_id for the template
    current_operator_id = str(driver.operator_id) if driver.operator_id else "0"
    current_app.logger.info(f"Rendering template with current_operator_id: {current_operator_id}")

    return render_template(
        'owner/edit_driver.html',
        title='Edit Driver',
        form=form,
        driver=driver,
        unread_messages_count=unread_messages_count,
        operator_requests_count=operator_requests_count,
        operator_choices=operator_choices,
        current_operator_id=current_operator_id
    )


@owner.route('/dashboard/owner/drivers/<int:driver_id>/delete', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def delete_driver(driver_id):
    """
    Delete a driver
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get driver
    driver = Driver.query.get_or_404(driver_id)

    # Ensure driver belongs to owner's company
    if driver.company_id != company_id:
        flash('You do not have permission to delete this driver.', "danger")
        return redirect(url_for('owner.drivers'))

    # Check if driver has active routes
    active_routes = Route.query.filter(
        Route.driver_id == driver_id,
        Route.status.in_([RouteStatus.PLANNED, RouteStatus.IN_PROGRESS])
    ).count()

    if active_routes > 0:
        flash('Cannot delete driver with active routes. Please reassign or complete routes first.', "danger")
        return redirect(url_for('owner.view_driver', driver_id=driver_id))

    try:
        # Get user for log
        username = driver.user.username

        # Delete the driver (and user cascade)
        db.session.delete(driver.user)
        db.session.commit()

        log_action(ActionType.DELETE, f"Deleted driver {username}", db)
        flash(f'Driver {username} deleted successfully!', "success")
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting driver: {str(e)}', "danger")

    return redirect(url_for('owner.drivers'))


@owner.route('/dashboard/owner/drivers/<int:driver_id>/activate', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def activate_driver(driver_id):
    """
    Activate a driver account
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get driver
    driver = Driver.query.get_or_404(driver_id)

    # Ensure driver belongs to owner's company
    if driver.company_id != company_id:
        flash('You do not have permission to modify this driver.', "danger")
        return redirect(url_for('owner.drivers'))

    try:
        # Activate the driver
        driver.user.is_active = True
        db.session.commit()
        log_action(ActionType.UPDATE, f"Activated driver {driver.user.username}", db)

        flash(f"Driver {driver.user.first_name} {driver.user.last_name} has been activated successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error activating driver: {str(e)}", "danger")

    return redirect(url_for('owner.view_driver', driver_id=driver_id))


@owner.route('/dashboard/owner/drivers/<int:driver_id>/deactivate', methods=['POST'])
@login_required
@role_required(UserRole.COMPANY_OWNER.value)
def deactivate_driver(driver_id):
    """
    Deactivate a driver account
    """
    if not current_user.company_owner or not current_user.company_owner.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.company_owner.company_id

    # Get driver
    driver = Driver.query.get_or_404(driver_id)

    # Ensure driver belongs to owner's company
    if driver.company_id != company_id:
        flash('You do not have permission to modify this driver.', "danger")
        return redirect(url_for('owner.drivers'))

    # Check for active routes
    active_routes = Route.query.filter(
        Route.driver_id == driver_id,
        Route.status.in_([RouteStatus.PLANNED, RouteStatus.IN_PROGRESS])
    ).count()

    try:
        # Deactivate the driver
        driver.user.is_active = False
        db.session.commit()
        log_action(ActionType.UPDATE, f"Deactivated driver {driver.user.username}", db)

        if active_routes > 0:
            flash(
                f"Driver {driver.user.first_name} {driver.user.last_name} has been deactivated, but has {active_routes} active route(s) that may be affected.",
                "warning")
        else:
            flash(f"Driver {driver.user.first_name} {driver.user.last_name} has been deactivated successfully.",
                  "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error deactivating driver: {str(e)}", "danger")

    return redirect(url_for('owner.view_driver', driver_id=driver_id))