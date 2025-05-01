from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from sqlalchemy import or_, func

from app import db
from models import UserRole, User, Manager, Operator, Driver, Company, CompanyOwner, Admin, Message
from models import Task, TaskStatus, Route, RouteStatus, ActionType, Log
from utils import role_required, log_action
from forms import CompanyForm, UserForm, EditUserForm

main = Blueprint('main', __name__)


@main.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role.value == 'ADMIN':
            return redirect(url_for('admin.dashboard'))
        elif current_user.role == UserRole.COMPANY_OWNER:
            return redirect(url_for('owner.dashboard'))
        elif current_user.role == UserRole.MANAGER:
            return redirect(url_for('main.manager_dashboard'))
        elif current_user.role == UserRole.OPERATOR:
            return redirect(url_for('main.operator_dashboard'))
        elif current_user.role == UserRole.DRIVER:
            return redirect(url_for('main.driver_dashboard'))

    return render_template('index.html', title='Welcome to CRM')


@main.route('/profile')
@login_required
def profile():
    return render_template('profile.html', title='My Profile')


@main.route('/dashboard/manager')
@login_required
@role_required(UserRole.MANAGER.value)
def manager_dashboard():
    """
    Dashboard for manager with team overview and tasks
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.manager.company_id

    # Get operator statistics
    operators = Operator.query.filter_by(
        manager_id=current_user.manager.id
    ).all()

    operator_count = len(operators)

    # Get driver statistics for operators managed by this manager
    driver_query = Driver.query.filter(
        Driver.operator_id.in_([op.id for op in operators]) if operators else False
    )
    driver_count = driver_query.count()

    # Get task statistics
    task_query = Task.query.filter_by(
        company_id=company_id,
        creator_id=current_user.id
    )
    active_tasks = task_query.filter(
        Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS])
    ).count()

    # Get route statistics for drivers under this manager's operators
    route_query = Route.query.filter(
        Route.driver_id.in_([d.id for d in driver_query.all()]) if driver_count > 0 else False
    )
    active_routes = route_query.filter(
        Route.status.in_([RouteStatus.PLANNED, RouteStatus.IN_PROGRESS])
    ).count()

    # Get team performance data
    # In a real application, this would query actual performance data
    # Here we'll generate sample data for demonstration
    team_performance = []

    for operator in operators:
        if operator.user:
            # Get tasks created by this operator
            operator_tasks = Task.query.filter_by(
                creator_id=operator.id
            ).count()

            operator_completed_tasks = Task.query.filter_by(
                creator_id=operator.id,
                status=TaskStatus.COMPLETED
            ).count()

            # Calculate completion rate
            completion_rate = (operator_completed_tasks / operator_tasks * 100) if operator_tasks > 0 else 0

            team_performance.append({
                'name': f"{operator.user.first_name} {operator.user.last_name}",
                'role': 'Operator',
                'assigned_tasks': operator_tasks,
                'completed_tasks': operator_completed_tasks,
                'completion_rate': round(completion_rate, 1)
            })

    # Get recent tasks
    recent_tasks = Task.query.filter_by(
        company_id=company_id,
        creator_id=current_user.id
    ).order_by(
        Task.created_at.desc()
    ).limit(5).all()

    log_action(ActionType.VIEW, "Viewed manager dashboard", db)

    return render_template(
        'manager/dashboard.html',
        title='Manager Dashboard',
        operator_count=operator_count,
        driver_count=driver_count,
        active_tasks=active_tasks,
        active_routes=active_routes,
        team_performance=team_performance,
        recent_tasks=recent_tasks,
        now = datetime.utcnow()
    )


@main.route('/dashboard/manager/operators')
@login_required
@role_required(UserRole.MANAGER.value)
def manager_operators():
    """
    Show the operators assigned to this manager
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.manager.company_id
    page = request.args.get("PAGE", 1, type=int)

    # Get operators assigned to this manager
    operators_query = Operator.query.filter_by(
        manager_id=current_user.manager.id
    ).join(User, Operator.id == User.id)

    # Apply sorting
    operators_query = operators_query.order_by(User.first_name)

    # Paginate results
    pagination = operators_query.paginate(page=page, per_page=10)
    operators = pagination.items

    log_action(ActionType.VIEW, "Viewed operators list", db)

    return render_template(
        'manager/operators.html',
        title='My Operators',
        operators=operators,
        pagination=pagination
    )


@main.route('/dashboard/manager/operators/<int:operator_id>')
@login_required
@role_required(UserRole.MANAGER.value)
def view_operator(operator_id):
    """
    View details of a specific operator
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    # Get operator
    operator = Operator.query.get_or_404(operator_id)

    # Check if the operator is assigned to this manager
    if operator.manager_id != current_user.manager.id:
        flash('This operator is not assigned to you.', "DANGER")
        return redirect(url_for('main.manager_operators'))

    # Get activity logs for this operator
    activity_logs = Log.query.filter_by(
        user_id=operator.id
    ).order_by(Log.timestamp.desc()).limit(10).all()

    # Get operators managed by this manager
    manager_operators = Operator.query.filter_by(manager_id=current_user.manager.id).all()
    manager_operator_ids = [op.id for op in manager_operators]

    # Get available drivers for assignment:
    # 1. Drivers not assigned to any operator (operator_id is None)
    # 2. Drivers assigned to operators managed by this manager
    available_drivers = Driver.query.filter(
        Driver.company_id == current_user.manager.company_id,
        or_(
            Driver.operator_id.is_(None),  # Not assigned to any operator
            Driver.operator_id.in_(manager_operator_ids) if manager_operator_ids else False  # Assigned to this manager's operators
        )
    ).all()

    # Get all operators for reassignment options
    operators = Operator.query.filter_by(
        company_id=current_user.manager.company_id
    ).all()

    log_action(ActionType.VIEW, f"Viewed operator {operator.user.username}", db)

    return render_template(
        'manager/view_operator.html',
        title=f'Operator: {operator.user.first_name} {operator.user.last_name}',
        operator=operator,
        activity_logs=activity_logs,
        available_drivers=available_drivers,
        operators=operators
    )


@main.route('/dashboard/manager/operators/add', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def add_operator():
    """
    Add a new operator to the team
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.manager.company_id

    # Get existing operators without a manager
    available_users_query = User.query.join(
        Operator, User.id == Operator.id
    ).filter(
        Operator.company_id == company_id,
        Operator.manager_id == None
    )

    available_users = available_users_query.all()

    if request.method == 'POST':
        # Handle form submission
        operator_id = request.form.get("OPERATOR_ID")

        if operator_id:
            try:
                # Assign existing operator to this manager
                operator = Operator.query.get(operator_id)

                if operator and operator.company_id == company_id:
                    operator.manager_id = current_user.manager.id
                    db.session.commit()

                    log_action(ActionType.UPDATE, f"Added operator {operator.user.username} to team", db)
                    flash(f'Operator {operator.user.first_name} {operator.user.last_name} added to your team!', "SUCCESS")
                    return redirect(url_for('main.manager_operators'))
                else:
                    flash('Invalid operator selected.', "DANGER")
            except Exception as e:
                db.session.rollback()
                flash(f'Error adding operator: {str(e)}', "DANGER")

    log_action(ActionType.VIEW, "Viewed add operator page", db)

    return render_template(
        'manager/add_operator.html',
        title='Add Operator',
        available_users=available_users
    )


@main.route('/dashboard/manager/operators/request', methods=['POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def request_operator():
    """
    Request a new operator account to be created
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    company_id = current_user.manager.company_id

    # Get form data
    first_name = request.form.get("FIRST_NAME")
    last_name = request.form.get("LAST_NAME")
    email = request.form.get("EMAIL")
    phone = request.form.get("PHONE")
    justification = request.form.get("JUSTIFICATION")

    if not first_name or not last_name or not email or not justification:
        flash('Please fill out all required fields.', "DANGER")
        return redirect(url_for('main.add_operator'))

    try:
        # Find company owner
        company_owner = CompanyOwner.query.filter_by(company_id=company_id).first()

        if not company_owner or not company_owner.user:
            # If no owner found, notify admin
            admins = Admin.query.join(User, Admin.id == User.id).filter(User.is_active == True).all()

            for admin in admins:
                # Create notification message to admin
                notification = Message(
                    content=f"Manager {current_user.first_name} {current_user.last_name} requests a new operator account:\n\n"
                            f"Name: {first_name} {last_name}\n"
                            f"Email: {email}\n"
                            f"Phone: {phone or 'Not provided'}\n\n"
                            f"Justification: {justification}",
                    sender_id=current_user.id,
                    recipient_id=admin.id,
                    task_id=None,
                    company_id=company_id,
                    is_read=False,
                    sent_at=datetime.utcnow()
                )

                db.session.add(notification)

            db.session.commit()
            log_action(ActionType.CREATE, "Requested new operator account (to admins)", db)
            flash('Your request has been sent to the system administrators.', "SUCCESS")
        else:
            # Create notification message to company owner
            notification = Message(
                content=f"Manager {current_user.first_name} {current_user.last_name} requests a new operator account:\n\n"
                        f"Name: {first_name} {last_name}\n"
                        f"Email: {email}\n"
                        f"Phone: {phone or 'Not provided'}\n"
                        f"Position: Operator\n"
                        f"Manager: {current_user.first_name} {current_user.last_name}\n"
                        f"Manager Email: {current_user.email}\n\n"
                        f"Justification: {justification}",
                sender_id=current_user.id,
                recipient_id=company_owner.user.id,
                task_id=None,
                company_id=company_id,
                is_read=False,
                sent_at=datetime.utcnow()
            )

            db.session.add(notification)
            db.session.commit()
            log_action(ActionType.CREATE, "Requested new operator account (to company owner)", db)
            flash('Your request has been sent to the company owner.', "SUCCESS")

    except Exception as e:
        db.session.rollback()
        flash(f'Error submitting request: {str(e)}', "DANGER")

    return redirect(url_for('main.manager_operators'))


@main.route('/dashboard/manager/operators/<int:operator_id>/remove', methods=['POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def remove_operator(operator_id):
    """
    Remove an operator from this manager's team
    """
    if not current_user.manager:
        flash('You are not authorized to perform this action.', "DANGER")
        return redirect(url_for('main.index'))

    # Get operator
    operator = Operator.query.get_or_404(operator_id)

    # Check if operator is assigned to this manager
    if operator.manager_id != current_user.manager.id:
        flash('This operator is not assigned to you.', "DANGER")
        return redirect(url_for('main.manager_operators'))

    try:
        # Store operator name for flash message
        operator_name = f"{operator.user.first_name} {operator.user.last_name}"

        # Remove manager assignment
        operator.manager_id = None
        db.session.commit()

        log_action(ActionType.UPDATE, f"Removed operator {operator.user.username} from team", db)
        flash(f'Operator {operator_name} removed from your team.', "SUCCESS")
    except Exception as e:
        db.session.rollback()
        flash(f'Error removing operator: {str(e)}', "DANGER")

    return redirect(url_for('main.manager_operators'))


@main.route('/dashboard/manager/operators/<int:operator_id>/assign-drivers', methods=['POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def assign_drivers(operator_id):
    """
    Assign drivers to an operator
    """
    if not current_user.manager:
        flash('You are not authorized to perform this action.', "DANGER")
        return redirect(url_for('main.index'))

    # Get operator
    operator = Operator.query.get_or_404(operator_id)

    # Check if operator is assigned to this manager
    if operator.manager_id != current_user.manager.id:
        flash('This operator is not assigned to you.', "DANGER")
        return redirect(url_for('main.manager_operators'))

    try:
        # Get selected driver IDs
        driver_ids = request.form.getlist('driver_ids[]')

        # Get all drivers in the company
        company_drivers = Driver.query.filter_by(
            company_id=current_user.manager.company_id
        ).all()

        # Update driver assignments
        for driver in company_drivers:
            if str(driver.id) in driver_ids:
                # Assign to this operator
                driver.operator_id = operator.id
            elif driver.operator_id == operator.id:
                # Unassign from this operator
                driver.operator_id = None

        db.session.commit()
        log_action(ActionType.UPDATE, f"Updated driver assignments for operator {operator.user.username}", db)

        flash('Driver assignments updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating driver assignments: {str(e)}', 'danger')

    return redirect(url_for('main.view_operator', operator_id=operator_id))


@main.route('/dashboard/manager/operators/<int:operator_id>/drivers/<int:driver_id>/unassign', methods=['POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def unassign_driver(operator_id, driver_id):
    """
    Remove a driver from an operator
    """
    if not current_user.manager:
        flash('You are not authorized to perform this action.', "DANGER")
        return redirect(url_for('main.index'))

    # Get operator and driver
    operator = Operator.query.get_or_404(operator_id)
    driver = Driver.query.get_or_404(driver_id)

    # Check if operator is assigned to this manager
    if operator.manager_id != current_user.manager.id:
        flash('This operator is not assigned to you.', "DANGER")
        return redirect(url_for('main.manager_operators'))

    # Check if driver is assigned to this operator
    if driver.operator_id != operator.id:
        flash('This driver is not assigned to this operator.', "DANGER")
        return redirect(url_for('main.view_operator', operator_id=operator_id))

    try:
        # Store names for flash message
        driver_name = f"{driver.user.first_name} {driver.user.last_name}"

        # Remove operator assignment
        driver.operator_id = None
        db.session.commit()

        log_action(ActionType.UPDATE, f"Unassigned driver {driver.user.username} from operator {operator.user.username}", db)
        flash(f'Driver {driver_name} unassigned from operator.', "SUCCESS")
    except Exception as e:
        db.session.rollback()
        flash(f'Error unassigning driver: {str(e)}', "DANGER")

    return redirect(url_for('main.view_operator', operator_id=operator_id))


@main.route('/dashboard/manager/tasks')
@login_required
@role_required(UserRole.MANAGER.value)
def manager_tasks():
    """
    Show task management interface for manager
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.manager.company_id
    page = request.args.get("page", 1, type=int)
    status = request.args.get("status")
    view = request.args.get("view", "team")  # team or my
    sort = request.args.get("sort", "deadline_asc")
    search_term = request.args.get("search", '')

    # Build query
    task_query = Task.query.filter_by(company_id=company_id)

    # Apply view filter
    if view == "my":
        task_query = task_query.filter_by(creator_id=current_user.id)
    else:
        # For team view, show only tasks created by users in manager's company
        # Get user IDs from the company
        company_user_ids = db.session.query(User.id).join(
            CompanyOwner, User.id == CompanyOwner.id, full=True
        ).join(
            Manager, User.id == Manager.id, full=True
        ).join(
            Operator, User.id == Operator.id, full=True
        ).join(
            Driver, User.id == Driver.id, full=True
        ).filter(
            or_(
                CompanyOwner.company_id == company_id,
                Manager.company_id == company_id,
                Operator.company_id == company_id,
                Driver.company_id == company_id
            )
        ).all()
        company_user_ids = [user_id[0] for user_id in company_user_ids]
        task_query = task_query.filter(Task.creator_id.in_(company_user_ids))

    # Apply status filter
    if status:
        try:
            task_status = TaskStatus(status)
            task_query = task_query.filter(Task.status == task_status)
        except ValueError:
            # Invalid status value, ignore filter
            pass

    # Apply search filter
    if search_term:
        search = f"%{search_term}%"
        task_query = task_query.filter(
            or_(
                Task.title.ilike(search),
                Task.description.ilike(search)
            )
        )

    # Apply sorting
    if sort == "deadline_asc":
        # Nulls last for deadline
        task_query = task_query.order_by(
            Task.deadline.is_(None).asc(),  # Not null deadlines first
            Task.deadline.asc()  # Then by deadline (soonest first)
        )
    elif sort == "deadline_desc":
        task_query = task_query.order_by(
            Task.deadline.is_(None).asc(),  # Not null deadlines first
            Task.deadline.desc()  # Then by deadline (latest first)
        )
    elif sort == "created_desc":
        task_query = task_query.order_by(Task.created_at.desc())
    elif sort == "created_asc":
        task_query = task_query.order_by(Task.created_at.asc())

    # Paginate results
    tasks = task_query.paginate(page=page, per_page=10)

    # Get task statistics
    # Update task statistics to also filter by company users
    all_tasks_query = Task.query.filter_by(company_id=company_id)
    if view != "my":  # Use the same company users filter for statistics if not in "my" view
        all_tasks_query = all_tasks_query.filter(Task.creator_id.in_(company_user_ids))
    all_tasks = all_tasks_query.all()

    total_tasks = len(all_tasks)
    new_tasks = sum(1 for t in all_tasks if t.status == TaskStatus.NEW)
    in_progress_tasks = sum(1 for t in all_tasks if t.status == TaskStatus.IN_PROGRESS)
    on_hold_tasks = sum(1 for t in all_tasks if t.status == TaskStatus.ON_HOLD)
    completed_tasks = sum(1 for t in all_tasks if t.status == TaskStatus.COMPLETED)
    cancelled_tasks = sum(1 for t in all_tasks if t.status == TaskStatus.CANCELLED)

    # Calculate completion rate
    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    # Calculate priorities (in a real app, these would be actual properties of tasks)
    # For demo purposes, we'll use deadlines as a proxy for priority
    high_priority = sum(1 for t in all_tasks if t.deadline and (t.deadline - datetime.utcnow()).total_seconds() < 86400)  # Within 24 hours
    medium_priority = sum(1 for t in all_tasks if t.deadline and 86400 <= (t.deadline - datetime.utcnow()).total_seconds() < 259200)  # 1-3 days
    low_priority = sum(1 for t in all_tasks if t.deadline and (t.deadline - datetime.utcnow()).total_seconds() >= 259200)  # 3+ days

    high_priority_percent = (high_priority / total_tasks * 100) if total_tasks > 0 else 0
    medium_priority_percent = (medium_priority / total_tasks * 100) if total_tasks > 0 else 0
    low_priority_percent = (low_priority / total_tasks * 100) if total_tasks > 0 else 0

    # Create task_stats with the required 'total' attribute
    task_stats = {
        "total": total_tasks,  # Add this to fix the UndefinedError
        "new": new_tasks,
        "in_progress": in_progress_tasks,
        "on_hold": on_hold_tasks,
        "completed": completed_tasks,
        "cancelled": cancelled_tasks,
        "completion_rate": round(completion_rate),
        "high_priority": high_priority,
        "medium_priority": medium_priority,
        "low_priority": low_priority,
        "high_priority_percent": round(high_priority_percent),
        "medium_priority_percent": round(medium_priority_percent),
        "low_priority_percent": round(low_priority_percent)
    }

    log_action(ActionType.VIEW, "Viewed tasks management", db)

    return render_template(
        'manager/tasks.html',
        title='Tasks Management',
        tasks=tasks,
        status=status,
        view=view,
        sort=sort,
        search_term=search_term,
        task_stats=task_stats,
        now=datetime.utcnow()
    )


@main.route('/dashboard/manager/reports')
@login_required
@role_required(UserRole.MANAGER.value)
def manager_reports():
    """
    Show performance reports for the manager's team
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    # Get date range from request, default to last 30 days
    period = request.args.get("PERIOD", '30')

    # Handle custom date range
    if period == "CUSTOM":
        start_date = request.args.get("START_DATE")
        end_date = request.args.get("END_DATE")

        if not start_date or not end_date:
            # Default to last 30 days if dates not provided
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=30)
        else:
            # Parse dates from string
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
            # Set end_date to end of day
            end_date = end_date.replace(hour=23, minute=59, second=59)
    else:
        # Calculate date range based on period
        days = int(period)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

    company_id = current_user.manager.company_id

    # Get key performance indicators
    # In a real application, this would query the database for actual stats
    # Here we'll generate sample data for demonstration

    # Get task count
    task_query = Task.query.filter(
        Task.company_id == company_id,
        Task.created_at.between(start_date, end_date)
    )
    total_tasks = task_query.count()
    completed_tasks = task_query.filter(Task.status == TaskStatus.COMPLETED).count()

    # Calculate completion rate
    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    # Calculate average completion time
    avg_completion_time = 0
    completed_tasks_with_times = []
    for task in task_query.filter(Task.status == TaskStatus.COMPLETED).all():
        if task.updated_at and task.created_at:
            completed_tasks_with_times.append((task.updated_at - task.created_at).total_seconds() / 3600)  # Hours

    if completed_tasks_with_times:
        avg_completion_time = sum(completed_tasks_with_times) / len(completed_tasks_with_times)

    # Get route count
    total_routes = Route.query.filter(
        Route.company_id == company_id,
        Route.start_time.between(start_date, end_date)
    ).count()

    # Create KPI object
    kpi = {
        'total_tasks': total_tasks,
        'completion_rate': round(completion_rate),
        'avg_completion_time': round(avg_completion_time, 1),
        'total_routes': total_routes
    }

    # Get team performance data
    # Get operators assigned to this manager
    operators = Operator.query.filter_by(manager_id=current_user.manager.id).all()

    team_performance = []

    # Add manager's own performance
    manager_tasks = Task.query.filter(
        Task.creator_id == current_user.id,
        Task.created_at.between(start_date, end_date)
    ).all()

    manager_assigned_tasks = len(manager_tasks)
    manager_completed_tasks = sum(1 for t in manager_tasks if t.status == TaskStatus.COMPLETED)

    team_performance.append({
        'name': f"{current_user.first_name} {current_user.last_name} (You)",
        'role': 'Manager',
        'assigned_tasks': manager_assigned_tasks,
        'completed_tasks': manager_completed_tasks
    })

    # Add operators' performance
    for operator in operators:
        if operator.user:
            operator_tasks = Task.query.filter(
                Task.creator_id == operator.id,
                Task.created_at.between(start_date, end_date)
            ).all()

            operator_assigned_tasks = len(operator_tasks)
            operator_completed_tasks = sum(1 for t in operator_tasks if t.status == TaskStatus.COMPLETED)

            team_performance.append({
                'name': f"{operator.user.first_name} {operator.user.last_name}",
                'role': 'Operator',
                'assigned_tasks': operator_assigned_tasks,
                'completed_tasks': operator_completed_tasks
            })

    # Add drivers' performance
    for operator in operators:
        for driver in operator.drivers:
            if driver.user:
                # For drivers, use assigned tasks and routes
                driver_tasks = Task.query.filter(
                    Task.assignee_id == driver.id,
                    Task.created_at.between(start_date, end_date)
                ).all()

                driver_assigned_tasks = len(driver_tasks)
                driver_completed_tasks = sum(1 for t in driver_tasks if t.status == TaskStatus.COMPLETED)

                team_performance.append({
                    'name': f"{driver.user.first_name} {driver.user.last_name}",
                    'role': 'Driver',
                    'assigned_tasks': driver_assigned_tasks,
                    'completed_tasks': driver_completed_tasks
                })

    log_action(ActionType.VIEW, "Viewed reports dashboard", db)

    return render_template(
        'manager/reports.html',
        title='Performance Reports',
        kpi=kpi,
        team_performance=team_performance,
        period=period,
        start_date=start_date.strftime('%Y-%m-%d') if hasattr(start_date, 'strftime') else '',
        end_date=end_date.strftime('%Y-%m-%d') if hasattr(end_date, 'strftime') else ''
    )

@main.route('/dashboard/manager/operators/<int:operator_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def edit_operator(operator_id):
    """
    Edit operator details
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    # Get operator
    operator = Operator.query.get_or_404(operator_id)

    # Check if operator is assigned to this manager
    if operator.manager_id != current_user.manager.id:
        flash('This operator is not assigned to you.', "DANGER")
        return redirect(url_for('main.manager_operators'))

    # Create form
    form = EditUserForm(
        original_username=operator.user.username,
        original_email=operator.user.email
    )

    if request.method == 'GET':
        form.username.data = operator.user.username
        form.email.data = operator.user.email
        form.first_name.data = operator.user.first_name
        form.last_name.data = operator.user.last_name
        form.phone.data = operator.user.phone
        form.is_active.data = operator.user.is_active

    if form.validate_on_submit():
        try:
            # Update user details
            operator.user.username = form.username.data
            operator.user.email = form.email.data
            operator.user.first_name = form.first_name.data
            operator.user.last_name = form.last_name.data
            operator.user.phone = form.phone.data
            operator.user.is_active = form.is_active.data

            # Handle profile image if provided
            if form.profile_image.data:
                from utils import save_profile_image
                operator.user.profile_image = save_profile_image(form.profile_image.data)

            # Update operator notes if present in the form
            if hasattr(form, "NOTES"):
                operator.notes = form.notes.data

            db.session.commit()
            log_action(ActionType.UPDATE, f"Updated operator {operator.user.username}", db)

            flash(f'Operator {operator.user.first_name} {operator.user.last_name} updated successfully!', "SUCCESS")
            return redirect(url_for('main.view_operator', operator_id=operator_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating operator: {str(e)}', "DANGER")

    return render_template(
        'manager/edit_operator.html',
        title='Edit Operator',
        form=form,
        operator=operator
    )


@main.route('/dashboard/manager/request-password-reset/<int:user_id>', methods=['POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def request_password_reset(user_id):
    """
    Request password reset for an operator
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not associated with a company.', "DANGER")
        return redirect(url_for('main.index'))

    # Get user
    user = User.query.get_or_404(user_id)

    # Check if user is an operator assigned to this manager
    operator = Operator.query.get(user_id)
    if not operator or operator.manager_id != current_user.manager.id:
        flash('You do not have permission to reset this user\'s password.', "DANGER")
        return redirect(url_for('main.manager_operators'))

    try:
        # In a real application, this would send a password reset email
        # For this demo, we'll just generate a random password and notify the manager
        import random
        import string

        # Generate random password
        temp_password = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(10))

        # Set new password
        user.set_password(temp_password)
        db.session.commit()

        # Create system message to notify operator
        message = Message(
            content=f"Your password has been reset by your manager. Please contact them for your temporary password.",
            sender_id=current_user.id,
            recipient_id=user.id,
            task_id=None,
            company_id=current_user.manager.company_id,
            is_read=False,
            sent_at=datetime.utcnow()
        )
        db.session.add(message)
        db.session.commit()

        log_action(ActionType.UPDATE, f"Requested password reset for {user.username}", db)

        flash(
            f'Password for {user.first_name} {user.last_name} has been reset to: {temp_password}. Please provide this to the operator securely.',
            "SUCCESS")

        # Redirect back to operator's profile
        if operator:
            return redirect(url_for('main.view_operator', operator_id=operator.id))
        else:
            return redirect(url_for('main.manager_operators'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error resetting password: {str(e)}', "DANGER")
        return redirect(url_for('main.manager_operators'))


@main.route('/dashboard/manager/operators/<int:operator_id>/drivers/<int:driver_id>/unassign', methods=['POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def unassign_driver_from_operator(operator_id, driver_id):
    """
    Remove a driver from an operator
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not authorized to perform this action.', "DANGER")
        return redirect(url_for('main.index'))

    # Get operator and driver
    operator = Operator.query.get_or_404(operator_id)
    driver = Driver.query.get_or_404(driver_id)

    # Check if operator is assigned to this manager
    if operator.manager_id != current_user.manager.id:
        flash('This operator is not assigned to you.', "DANGER")
        return redirect(url_for('main.manager_operators'))

    # Check if driver is assigned to this operator
    if driver.operator_id != operator.id:
        flash('This driver is not assigned to this operator.', "DANGER")
        return redirect(url_for('main.view_operator', operator_id=operator_id))

    try:
        # Store names for flash message
        driver_name = f"{driver.user.first_name} {driver.user.last_name}"

        # Remove operator assignment
        driver.operator_id = None
        db.session.commit()

        log_action(ActionType.UPDATE, f"Unassigned driver {driver.user.username} from operator {operator.user.username}", db)
        flash(f'Driver {driver_name} unassigned from operator.', "SUCCESS")
    except Exception as e:
        db.session.rollback()
        flash(f'Error unassigning driver: {str(e)}', "DANGER")

    return redirect(url_for('main.view_operator', operator_id=operator_id))


@main.route('/dashboard/manager/operators/<int:operator_id>/assign-drivers', methods=['POST'])
@main.route('/dashboard/manager/operators/<int:operator_id>/assign-drivers', methods=['POST'])
@login_required
@role_required(UserRole.MANAGER.value)
def assign_drivers_to_operator(operator_id):
    """
    Assign drivers to an operator
    """
    if not current_user.manager or not current_user.manager.company_id:
        flash('You are not authorized to perform this action.', "DANGER")
        return redirect(url_for('main.index'))

    # Get operator
    operator = Operator.query.get_or_404(operator_id)

    # Check if operator is assigned to this manager
    if operator.manager_id != current_user.manager.id:
        flash('This operator is not assigned to you.', "DANGER")
        return redirect(url_for('main.manager_operators'))

    try:
        # Get selected driver IDs
        driver_ids = request.form.getlist('driver_ids[]')

        # Get all drivers in the company
        company_drivers = Driver.query.filter_by(
            company_id=current_user.manager.company_id
        ).all()

        # Update driver assignments
        for driver in company_drivers:
            if str(driver.id) in driver_ids:
                # Assign to this operator
                driver.operator_id = operator.id
            elif driver.operator_id == operator.id:
                # Unassign from this operator
                driver.operator_id = None

        db.session.commit()
        log_action(ActionType.UPDATE, f"Updated driver assignments for operator {operator.user.username}", db)

        flash('Driver assignments updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating driver assignments: {str(e)}', 'danger')

    return redirect(url_for('main.view_operator', operator_id=operator_id))


@main.route('/dashboard/operator')
@login_required
@role_required(UserRole.OPERATOR.value)
def operator_dashboard():
    """
    Dashboard for operator showing their tasks and assigned drivers
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.operator.company_id

    # Get assigned drivers
    drivers = Driver.query.filter_by(operator_id=current_user.operator.id).all()
    driver_count = len(drivers)

    # Get task statistics for tasks created by this operator
    task_query = Task.query.filter_by(
        creator_id=current_user.id,
        company_id=company_id
    )

    total_tasks = task_query.count()
    new_tasks = task_query.filter_by(status=TaskStatus.NEW).count()
    in_progress_tasks = task_query.filter_by(status=TaskStatus.IN_PROGRESS).count()
    active_tasks = new_tasks + in_progress_tasks
    completed_tasks = task_query.filter_by(status=TaskStatus.COMPLETED).count()

    # Get routes for drivers managed by this operator
    driver_ids = [d.id for d in drivers]

    route_query = Route.query.filter(
        Route.driver_id.in_(driver_ids) if driver_ids else False
    )

    total_routes = route_query.count()
    active_routes = route_query.filter(
        Route.status.in_([RouteStatus.PLANNED, RouteStatus.IN_PROGRESS])
    ).count()
    completed_routes = route_query.filter_by(status=RouteStatus.COMPLETED).count()

    # Get unread messages count
    unread_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get manager info
    manager = None
    if current_user.operator and current_user.operator.manager_id:
        manager_user = User.query.get(current_user.operator.manager_id)
        if manager_user:
            manager = {
                'id': manager_user.id,
                'name': f"{manager_user.first_name} {manager_user.last_name}",
                'email': manager_user.email,
                'phone': manager_user.phone
            }

    log_action(ActionType.VIEW, "Viewed operator dashboard", db)

    return render_template(
        'operator/dashboard.html',
        title='Operator Dashboard',
        drivers=drivers,
        driver_count=driver_count,
        total_tasks=total_tasks,
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        total_routes=total_routes,
        active_routes=active_routes,
        completed_routes=completed_routes,
        unread_count=unread_count,
        manager=manager
    )


@main.route('/dashboard/driver')
@login_required
@role_required(UserRole.DRIVER.value)
def driver_dashboard():
    """
    Dashboard for driver showing their tasks and routes
    """
    if not current_user.driver or not current_user.driver.company_id:
        flash('You are not associated with a company.', "danger")
        return redirect(url_for('main.index'))

    company_id = current_user.driver.company_id

    # Get task statistics for driver
    task_query = Task.query.filter_by(
        assignee_id=current_user.id
    )

    active_tasks = task_query.filter(
        Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS])
    ).count()

    new_tasks = task_query.filter_by(status=TaskStatus.NEW).count()
    in_progress_tasks = task_query.filter_by(status=TaskStatus.IN_PROGRESS).count()
    completed_tasks = task_query.filter_by(status=TaskStatus.COMPLETED).count()

    # Get route statistics
    route_query = Route.query.filter_by(
        driver_id=current_user.driver.id
    )

    active_routes = route_query.filter(
        Route.status.in_([RouteStatus.PLANNED, RouteStatus.IN_PROGRESS])
    ).count()

    completed_routes = route_query.filter_by(status=RouteStatus.COMPLETED).count()

    # Get total distance driven
    total_distance = db.session.query(func.sum(Route.distance)).filter(
        Route.driver_id == current_user.driver.id,
        Route.status == RouteStatus.COMPLETED
    ).scalar() or 0

    # Get current active task
    active_task = task_query.filter_by(status=TaskStatus.IN_PROGRESS).first()

    # Get current active route
    active_route = route_query.filter_by(status=RouteStatus.IN_PROGRESS).first()

    # Get next planned route
    next_route = route_query.filter_by(status=RouteStatus.PLANNED).order_by(Route.start_time).first()

    # Get unread messages count
    unread_count = Message.query.filter_by(
        recipient_id=current_user.id,
        is_read=False
    ).count()

    # Get operator info
    operator = None
    if current_user.driver.operator_id:
        operator_user = User.query.get(current_user.driver.operator_id)
        if operator_user:
            operator = {
                'id': operator_user.id,
                'name': f"{operator_user.first_name} {operator_user.last_name}",
                'email': operator_user.email,
                'phone': operator_user.phone
            }

    # Get recent messages
    recent_messages = Message.query.filter_by(
        recipient_id=current_user.id
    ).order_by(Message.sent_at.desc()).limit(5).all()

    # Calculate statistics for driver performance metrics
    stats = {
        'new': new_tasks,
        'active': in_progress_tasks,
        'completed_tasks': completed_tasks,
        'active_routes': active_routes,
        'completed_routes': completed_routes,
        'total_distance': total_distance,
        'performance_score': 85,  # This would be calculated based on actual metrics
        'on_time_rate': 92,  # This would be calculated from historical data
        'avg_completion_time': 120  # This would be calculated from historical data
    }

    log_action(ActionType.VIEW, "Viewed driver dashboard", db)

    now = datetime.utcnow()  # Current time for deadline checks

    return render_template(
        'driver/dashboard.html',
        title='Driver Dashboard',
        active_tasks=active_tasks,
        active_routes=active_routes,
        active_task=active_task,
        active_route=active_route,
        next_route=next_route,
        stats=stats,
        unread_count=unread_count,
        operator=operator,
        recent_messages=recent_messages,
        now=now
    )