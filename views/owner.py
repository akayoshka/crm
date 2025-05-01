from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from sqlalchemy import func, or_, and_
from models import Message, ActionType

from app import db
from models import (
    User, UserRole, Manager, Operator, Driver, Company, CompanyOwner, Admin, Message,
    Task, TaskStatus, Route, RouteStatus, ActionType, Log
)
from utils import role_required, log_action
from forms import CompanyForm, UserForm, EditUserForm

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
    # The message has a format like:
    # Manager <name> requests a new operator account:
    # Name: <first_name> <last_name>
    # Email: <email>
    # Phone: <phone or 'Not provided'>
    # Justification: <text>

    # Extract details from the message content
    lines = message.content.split('\n')
    name_line = next((line for line in lines if line.startswith('Name:')), None)
    email_line = next((line for line in lines if line.startswith('Email:')), None)
    phone_line = next((line for line in lines if line.startswith('Phone:')), None)

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

        # Redirect to create operator form with pre-filled data
        return redirect(url_for(
            'admin.create_operator',
            manager_id=manager_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone
        ))
    else:
        flash('Could not extract information from the request. Please create operator manually.', "warning")
        return redirect(url_for('admin.create_operator'))


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