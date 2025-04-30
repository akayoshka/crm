from typing import Optional

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from sqlalchemy import func, or_, and_
from wtforms import SelectField

from app import db
from models import (
    User, Route, RouteStatus, Task, TaskStatus, Driver, Message,
    Document, UserRole, ActionType, Log, Company
)
from forms import DocumentUploadForm, TaskForm, MessageForm
from utils import role_required, log_action, save_document
from wtforms.validators import Optional

from services import TaskService, RouteService

# Create blueprint
operator = Blueprint('operator', __name__, url_prefix='/operator')


@operator.route('/drivers')
@login_required
@role_required(UserRole.OPERATOR.value)
def drivers():
    """
    Show drivers assigned to this operator
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', None)
    sort = request.args.get('sort', 'name_asc')
    search_term = request.args.get('search', '')

    # Get drivers assigned to this operator
    driver_query = Driver.query.filter_by(operator_id=current_user.operator.id)

    # Apply status filter
    if status == 'active':
        driver_query = driver_query.join(User, User.id == Driver.id).filter(User.is_active == True)
    elif status == 'idle':
        # Idle drivers are those not on a route
        driver_query = driver_query.outerjoin(
            Route, and_(
                Route.driver_id == Driver.id,
                Route.status == RouteStatus.IN_PROGRESS
            )
        ).filter(Route.id == None)
    elif status == 'on_route':
        # Drivers on a route
        driver_query = driver_query.join(
            Route, and_(
                Route.driver_id == Driver.id,
                Route.status == RouteStatus.IN_PROGRESS
            )
        )

    # Apply search filter
    if search_term:
        driver_query = driver_query.join(User, User.id == Driver.id).filter(
            or_(
                User.first_name.ilike(f'%{search_term}%'),
                User.last_name.ilike(f'%{search_term}%'),
                User.email.ilike(f'%{search_term}%'),
                User.phone.ilike(f'%{search_term}%'),
                Driver.vehicle_info.ilike(f'%{search_term}%')
            )
        )

    # Apply sorting
    if sort == 'name_asc':
        driver_query = driver_query.join(User, User.id == Driver.id).order_by(User.first_name, User.last_name)
    elif sort == 'name_desc':
        driver_query = driver_query.join(User, User.id == Driver.id).order_by(User.first_name.desc(),
                                                                              User.last_name.desc())
    elif sort == 'tasks_desc':
        # This would need a more complex query with task counts
        driver_query = driver_query.join(User, User.id == Driver.id).order_by(User.first_name, User.last_name)
    elif sort == 'last_active_desc':
        # Could use last login time or last activity from logs
        driver_query = driver_query.join(User, User.id == Driver.id).order_by(User.last_login.desc().nullslast())

    # Paginate results
    drivers = driver_query.paginate(page=page, per_page=10)

    # Get stats for top drivers (for chart)
    top_drivers = []
    all_drivers = Driver.query.filter_by(operator_id=current_user.operator.id).all()

    for driver in all_drivers[:5]:  # Limit to 5 for the chart
        if not driver.user:
            continue

        # Get completed routes count
        completed_routes = Route.query.filter(
            Route.driver_id == driver.id,
            Route.status == RouteStatus.COMPLETED
        ).count()

        # Get total routes count
        total_routes = Route.query.filter(
            Route.driver_id == driver.id
        ).count()

        # Calculate completion rate
        completion_rate = (completed_routes / total_routes * 100) if total_routes > 0 else 0

        # Add to top drivers list
        top_drivers.append({
            'user': driver.user,
            'stats': {
                'completion_rate': round(completion_rate, 1)
            }
        })

    # Get active drivers (currently on route)
    active_drivers = Driver.query.filter_by(operator_id=current_user.operator.id).join(
        Route, and_(
            Route.driver_id == Driver.id,
            Route.status == RouteStatus.IN_PROGRESS
        )
    ).all()

    log_action(ActionType.VIEW, "Viewed drivers list", db)

    return render_template(
        'operator/drivers.html',
        title='My Drivers',
        drivers=drivers,
        status=status,
        sort=sort,
        search_term=search_term,
        top_drivers=top_drivers,
        active_drivers=active_drivers
    )


@operator.route('/tasks')
@login_required
@role_required(UserRole.OPERATOR.value)
def tasks():
    """
    Show tasks created by this operator
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', None)
    show_all = request.args.get('show_all', 'false').lower() == 'true'
    driver_id = request.args.get('driver_id', None, type=int)
    sort = request.args.get('sort', 'deadline_asc')
    search_term = request.args.get('search', '')

    # Build task query
    task_query = Task.query.filter_by(creator_id=current_user.id)

    # Apply status filter
    if status:
        try:
            task_status = TaskStatus(status)
            task_query = task_query.filter(Task.status == task_status)
        except ValueError:
            # Invalid status, ignore filter
            pass
    elif not show_all:
        # By default, exclude completed tasks
        task_query = task_query.filter(Task.status != TaskStatus.COMPLETED)

    # Apply driver filter
    if driver_id:
        task_query = task_query.filter(Task.assignee_id == driver_id)

    # Apply search filter
    if search_term:
        task_query = task_query.filter(
            or_(
                Task.title.ilike(f'%{search_term}%'),
                Task.description.ilike(f'%{search_term}%')
            )
        )

    # Apply sorting
    if sort == 'deadline_asc':
        task_query = task_query.order_by(
            Task.deadline.is_(None).asc(),  # Not null first
            Task.deadline.asc()  # Then by deadline (soonest first)
        )
    elif sort == 'deadline_desc':
        task_query = task_query.order_by(
            Task.deadline.is_(None).asc(),  # Not null first
            Task.deadline.desc()  # Then by deadline (latest first)
        )
    elif sort == 'created_desc':
        task_query = task_query.order_by(Task.created_at.desc())
    elif sort == 'created_asc':
        task_query = task_query.order_by(Task.created_at.asc())

    # Paginate results
    tasks = task_query.paginate(page=page, per_page=10)

    # Get all drivers for filter dropdown
    drivers = Driver.query.filter_by(operator_id=current_user.operator.id).all()

    # Get task stats
    all_tasks = Task.query.filter_by(creator_id=current_user.id).all()

    task_stats = {
        'new': sum(1 for t in all_tasks if t.status == TaskStatus.NEW),
        'in_progress': sum(1 for t in all_tasks if t.status == TaskStatus.IN_PROGRESS),
        'on_hold': sum(1 for t in all_tasks if t.status == TaskStatus.ON_HOLD),
        'completed': sum(1 for t in all_tasks if t.status == TaskStatus.COMPLETED),
        'cancelled': sum(1 for t in all_tasks if t.status == TaskStatus.CANCELLED)
    }

    # Get upcoming tasks with deadline
    upcoming_tasks = Task.query.filter(
        Task.creator_id == current_user.id,
        Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS]),
        Task.deadline.isnot(None)
    ).order_by(Task.deadline).limit(5).all()

    log_action(ActionType.VIEW, "Viewed tasks list", db)

    return render_template(
        'operator/tasks.html',
        title='Tasks',
        tasks=tasks,
        status=status,
        driver_id=driver_id,
        sort=sort,
        search_term=search_term,
        drivers=drivers,
        task_stats=task_stats,
        upcoming_tasks=upcoming_tasks,
        now=datetime.utcnow(),
        show_all=show_all
    )


@operator.route('/routes')
@login_required
@role_required(UserRole.OPERATOR.value)
def routes():
    """
    Show routes for drivers managed by this operator
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', None)
    driver_id = request.args.get('driver_id', None, type=int)
    date_from = request.args.get('date_from', None)
    date_to = request.args.get('date_to', None)
    search_term = request.args.get('search', '')

    # Get driver IDs for this operator
    operator_driver_ids = [d.id for d in Driver.query.filter_by(operator_id=current_user.operator.id).all()]

    # Build route query - only include routes for drivers managed by this operator
    route_query = Route.query.filter(Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False)

    # Apply status filter
    if status:
        try:
            route_status = RouteStatus(status)
            route_query = route_query.filter(Route.status == route_status)
        except ValueError:
            # Invalid status, ignore filter
            pass

    # Apply driver filter
    if driver_id:
        route_query = route_query.filter(Route.driver_id == driver_id)

    # Apply date range filters
    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            route_query = route_query.filter(Route.start_time >= from_date)
        except ValueError:
            # Invalid date format, ignore filter
            pass

    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d')
            # Set to end of day
            to_date = to_date.replace(hour=23, minute=59, second=59)
            route_query = route_query.filter(Route.start_time <= to_date)
        except ValueError:
            # Invalid date format, ignore filter
            pass

    # Apply search filter
    if search_term:
        route_query = route_query.filter(
            or_(
                Route.start_point.ilike(f'%{search_term}%'),
                Route.end_point.ilike(f'%{search_term}%')
            )
        )

    # Order by start time
    route_query = route_query.order_by(Route.start_time.desc())

    # Paginate results
    routes = route_query.paginate(page=page, per_page=10)

    # Get all drivers for filter dropdown
    drivers = Driver.query.filter_by(operator_id=current_user.operator.id).all()

    # Get route stats
    all_routes = Route.query.filter(Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False).all()

    route_stats = {
        'planned': sum(1 for r in all_routes if r.status == RouteStatus.PLANNED),
        'in_progress': sum(1 for r in all_routes if r.status == RouteStatus.IN_PROGRESS),
        'completed': sum(1 for r in all_routes if r.status == RouteStatus.COMPLETED),
        'cancelled': sum(1 for r in all_routes if r.status == RouteStatus.CANCELLED)
    }

    # Get active routes
    active_routes = Route.query.filter(
        Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
        Route.status == RouteStatus.IN_PROGRESS
    ).order_by(Route.start_time).all()

    # Get upcoming routes
    upcoming_routes = Route.query.filter(
        Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
        Route.status == RouteStatus.PLANNED
    ).order_by(Route.start_time).limit(5).all()

    log_action(ActionType.VIEW, "Viewed routes list", db)

    return render_template(
        'operator/routes.html',
        title='Routes',
        routes=routes,
        status=status,
        driver_id=driver_id,
        drivers=drivers,
        date_from=date_from,
        date_to=date_to,
        search_term=search_term,
        route_stats=route_stats,
        active_routes=active_routes,
        upcoming_routes=upcoming_routes
    )


@operator.route('/documents')
@login_required
@role_required(UserRole.OPERATOR.value)
def documents():
    """
    Show documents related to this operator's tasks and drivers
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    page = request.args.get('page', 1, type=int)
    search_term = request.args.get('title', '')
    file_type = request.args.get('file_type', '')
    task_id = request.args.get('task_id', None, type=int)

    # Get driver IDs for this operator
    operator_driver_ids = [d.id for d in Driver.query.filter_by(operator_id=current_user.operator.id).all()]

    # Get task IDs created by this operator
    operator_task_ids = [t.id for t in Task.query.filter_by(creator_id=current_user.id).all()]

    # Build document query - include:
    # 1. Documents uploaded by this operator
    # 2. Documents for tasks created by this operator
    # 3. Documents for routes of drivers managed by this operator
    document_query = Document.query.filter(
        or_(
            Document.uploader_id == current_user.id,
            Document.task_id.in_(operator_task_ids) if operator_task_ids else False
        )
    )

    # Apply search filter
    if search_term:
        document_query = document_query.filter(Document.title.ilike(f'%{search_term}%'))

    # Apply file type filter
    if file_type:
        document_query = document_query.filter(Document.file_type == file_type)

    # Apply task filter
    if task_id:
        document_query = document_query.filter(Document.task_id == task_id)

    # Order by upload time (newest first)
    document_query = document_query.order_by(Document.uploaded_at.desc())

    # Paginate results
    documents = document_query.paginate(page=page, per_page=10)

    # Get tasks for filter dropdown
    tasks = Task.query.filter_by(creator_id=current_user.id).all()

    # Create search form for template
    from forms import DocumentSearchForm
    search_form = DocumentSearchForm()

    # Get file types for dropdown
    file_types = db.session.query(Document.file_type).distinct().all()
    search_form.file_type.choices = [('', 'All Types')] + [(ft[0], ft[0].upper()) for ft in file_types]

    log_action(ActionType.VIEW, "Viewed documents list", db)

    return render_template(
        'operator/documents.html',
        title='Documents',
        documents=documents,
        search_form=search_form,
        tasks=tasks,
        task_id=task_id
    )


@operator.route('/statistics')
@login_required
@role_required(UserRole.OPERATOR.value)
def statistics():
    """
    Show performance statistics for drivers and tasks
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    # Get date range from request, default to last 30 days
    period = request.args.get('period', '30')
    report_type = request.args.get('report_type', 'drivers')
    driver_id = request.args.get('driver_id', None, type=int)

    # Handle custom date range
    if period == 'custom':
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if not start_date or not end_date:
            # Default to last 30 days if dates not provided
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=30)
        else:
            # Parse dates from string
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                # Set end_date to end of day
                end_date = end_date.replace(hour=23, minute=59, second=59)
            except ValueError:
                end_date = datetime.utcnow()
                start_date = end_date - timedelta(days=30)
    else:
        # Calculate date range based on period
        days = int(period)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

    # Get driver IDs for this operator
    operator_driver_ids = [d.id for d in Driver.query.filter_by(operator_id=current_user.operator.id).all()]

    # Get basic stats for KPI cards
    total_tasks = Task.query.filter(
        Task.creator_id == current_user.id,
        Task.created_at.between(start_date, end_date)
    ).count()

    completed_tasks = Task.query.filter(
        Task.creator_id == current_user.id,
        Task.status == TaskStatus.COMPLETED,
        Task.created_at.between(start_date, end_date)
    ).count()

    total_routes = Route.query.filter(
        Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
        Route.created_at.between(start_date, end_date)
    ).count()

    # Calculate total distance
    total_distance = db.session.query(func.sum(Route.distance)).filter(
        Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
        Route.status == RouteStatus.COMPLETED,
        Route.created_at.between(start_date, end_date)
    ).scalar() or 0

    # Create KPI summary
    kpi = {
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'total_routes': total_routes,
        'total_distance': total_distance
    }

    # Get drivers for filter dropdown
    drivers = Driver.query.filter_by(operator_id=current_user.operator.id).all()

    # Get stats based on report type
    if report_type == 'drivers':
        # Get driver performance stats
        driver_stats = []

        for driver in drivers:
            if not driver.user:
                continue

            # Get completed tasks
            driver_tasks = Task.query.filter(
                Task.assignee_id == driver.id,
                Task.created_at.between(start_date, end_date)
            ).all()

            total_tasks_count = len(driver_tasks)
            completed_tasks_count = sum(1 for t in driver_tasks if t.status == TaskStatus.COMPLETED)

            # Get completed routes
            driver_routes = Route.query.filter(
                Route.driver_id == driver.id,
                Route.created_at.between(start_date, end_date)
            ).all()

            completed_routes = sum(1 for r in driver_routes if r.status == RouteStatus.COMPLETED)

            # Calculate completion rate
            completion_rate = (completed_tasks_count / total_tasks_count * 100) if total_tasks_count > 0 else 0

            # Calculate on-time rate (for demo, we'll use a random value)
            # In a real app, you would compare actual completion times with deadlines
            import random
            on_time_rate = random.randint(60, 100)

            # Calculate total distance
            driver_distance = sum(r.distance or 0 for r in driver_routes if r.status == RouteStatus.COMPLETED)

            # Calculate average completion time
            completion_times = []
            for task in driver_tasks:
                if task.status == TaskStatus.COMPLETED and task.created_at and task.updated_at:
                    duration = (task.updated_at - task.created_at).total_seconds() / 60  # in minutes
                    completion_times.append(duration)

            avg_completion_time = sum(completion_times) / len(completion_times) if completion_times else 0

            driver_stats.append({
                'id': driver.id,
                'user': driver.user,
                'stats': {
                    'total_tasks': total_tasks_count,
                    'completed_tasks': completed_tasks_count,
                    'completion_rate': round(completion_rate, 1),
                    'avg_completion_time': round(avg_completion_time),
                    'total_distance': driver_distance,
                    'on_time_rate': on_time_rate
                }
            })

        # Get overall task stats
        task_stats = {
            'new': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.NEW,
                Task.created_at.between(start_date, end_date)
            ).count(),
            'in_progress': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.IN_PROGRESS,
                Task.created_at.between(start_date, end_date)
            ).count(),
            'on_hold': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.ON_HOLD,
                Task.created_at.between(start_date, end_date)
            ).count(),
            'completed': completed_tasks,
            'cancelled': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.CANCELLED,
                Task.created_at.between(start_date, end_date)
            ).count()
        }

        # Get on-time delivery stats
        on_time_stats = {
            'on_time': 0,
            'late': 0
        }

        # In a real app, you would calculate actual on-time deliveries
        # For demo, we'll use random values
        on_time_stats['on_time'] = int(total_routes * 0.85)
        on_time_stats['late'] = total_routes - on_time_stats['on_time']

    elif report_type == 'routes':
        # Get route status stats
        route_stats = {
            'planned': Route.query.filter(
                Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
                Route.status == RouteStatus.PLANNED,
                Route.created_at.between(start_date, end_date)
            ).count(),
            'in_progress': Route.query.filter(
                Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
                Route.status == RouteStatus.IN_PROGRESS,
                Route.created_at.between(start_date, end_date)
            ).count(),
            'completed': Route.query.filter(
                Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
                Route.status == RouteStatus.COMPLETED,
                Route.created_at.between(start_date, end_date)
            ).count(),
            'cancelled': Route.query.filter(
                Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
                Route.status == RouteStatus.CANCELLED,
                Route.created_at.between(start_date, end_date)
            ).count()
        }

        # Get daily route stats
        from sqlalchemy import func, cast, Date
        daily_routes = []

        # Get dates in range
        current_date = start_date.date()
        end_date_only = end_date.date()

        while current_date <= end_date_only:
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())

            created_count = Route.query.filter(
                Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
                Route.created_at.between(day_start, day_end)
            ).count()

            completed_count = Route.query.filter(
                Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
                Route.status == RouteStatus.COMPLETED,
                Route.end_time.between(day_start, day_end)
            ).count()

            daily_routes.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'created': created_count,
                'completed': completed_count
            })

            current_date += timedelta(days=1)

        # Get route completion time stats by driver
        route_time_stats = []

        for driver in drivers:
            if not driver.user:
                continue

            # Get average completion time for routes
            avg_time_query = db.session.query(
                func.avg(
                    func.extract('epoch', Route.end_time) -
                    func.extract('epoch', Route.actual_start_time)
                ) / 60  # Convert to minutes
            ).filter(
                Route.driver_id == driver.id,
                Route.status == RouteStatus.COMPLETED,
                Route.actual_start_time.isnot(None),
                Route.end_time.isnot(None),
                Route.created_at.between(start_date, end_date)
            )

            avg_time = avg_time_query.scalar() or 0

            route_time_stats.append({
                'name': f"{driver.user.first_name} {driver.user.last_name}",
                'avg_time': round(avg_time)
            })

        # Get recent routes
        recent_routes = Route.query.filter(
            Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
            Route.created_at.between(start_date, end_date)
        ).order_by(Route.created_at.desc()).limit(10).all()

        # Add on-time flag to completed routes
        for route in recent_routes:
            if route.status == RouteStatus.COMPLETED and route.start_time and route.end_time:
                # For demo, we'll randomly decide if a route was on time
                # In a real app, you would compare actual end time with planned end time
                import random
                route.is_on_time = random.choice([True, True, True, False])  # 75% on time

    elif report_type == 'tasks':
        # Get task status stats
        task_stats = {
            'new': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.NEW,
                Task.created_at.between(start_date, end_date)
            ).count(),
            'in_progress': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.IN_PROGRESS,
                Task.created_at.between(start_date, end_date)
            ).count(),
            'on_hold': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.ON_HOLD,
                Task.created_at.between(start_date, end_date)
            ).count(),
            'completed': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.COMPLETED,
                Task.created_at.between(start_date, end_date)
            ).count(),
            'cancelled': Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.CANCELLED,
                Task.created_at.between(start_date, end_date)
            ).count()
        }

        # Get daily task stats
        daily_tasks = []

        # Get dates in range
        current_date = start_date.date()
        end_date_only = end_date.date()

        while current_date <= end_date_only:
            day_start = datetime.combine(current_date, datetime.min.time())
            day_end = datetime.combine(current_date, datetime.max.time())

            created_count = Task.query.filter(
                Task.creator_id == current_user.id,
                Task.created_at.between(day_start, day_end)
            ).count()

            completed_count = Task.query.filter(
                Task.creator_id == current_user.id,
                Task.status == TaskStatus.COMPLETED,
                Task.updated_at.between(day_start, day_end)
            ).count()

            daily_tasks.append({
                'date': current_date.strftime('%Y-%m-%d'),
                'created': created_count,
                'completed': completed_count
            })

            current_date += timedelta(days=1)

        # Get task completion time distribution
        task_time_stats = {
            'less_than_4h': 0,
            'four_to_8h': 0,
            'eight_to_24h': 0,
            'one_to_2d': 0,
            'two_to_5d': 0,
            'more_than_5d': 0
        }

        # Get completed tasks
        completed_tasks = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.COMPLETED,
            Task.created_at.between(start_date, end_date),
            Task.updated_at.isnot(None)
        ).all()

        for task in completed_tasks:
            duration_hours = (task.updated_at - task.created_at).total_seconds() / 3600

            if duration_hours < 4:
                task_time_stats['less_than_4h'] += 1
            elif duration_hours < 8:
                task_time_stats['four_to_8h'] += 1
            elif duration_hours < 24:
                task_time_stats['eight_to_24h'] += 1
            elif duration_hours < 48:
                task_time_stats['one_to_2d'] += 1
            elif duration_hours < 120:
                task_time_stats['two_to_5d'] += 1
            else:
                task_time_stats['more_than_5d'] += 1

        # Get task distribution by driver
        task_distribution = []

        for driver in drivers:
            if not driver.user:
                continue

            task_count = Task.query.filter(
                Task.creator_id == current_user.id,
                Task.assignee_id == driver.id,
                Task.created_at.between(start_date, end_date)
            ).count()

            if task_count > 0:
                task_distribution.append({
                    'name': f"{driver.user.first_name} {driver.user.last_name}",
                    'count': task_count
                })

        # Get task metrics
        # Last 7 days
        seven_days_ago = datetime.utcnow() - timedelta(days=7)

        created_7_days = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.created_at >= seven_days_ago
        ).count()

        completed_7_days = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.COMPLETED,
            Task.updated_at >= seven_days_ago
        ).count()

        completion_rate_7_days = (completed_7_days / created_7_days * 100) if created_7_days > 0 else 0

        # Average completion time for last 7 days
        avg_time_7_days_tasks = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.COMPLETED,
            Task.created_at >= seven_days_ago,
            Task.updated_at.isnot(None)
        ).all()

        avg_time_7_days_hours = 0
        if avg_time_7_days_tasks:
            total_hours = sum((t.updated_at - t.created_at).total_seconds() / 3600 for t in avg_time_7_days_tasks)
            avg_time_7_days_hours = total_hours / len(avg_time_7_days_tasks)

        # Last 30 days
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)

        created_30_days = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.created_at >= thirty_days_ago
        ).count()

        completed_30_days = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.COMPLETED,
            Task.updated_at >= thirty_days_ago
        ).count()

        completion_rate_30_days = (completed_30_days / created_30_days * 100) if created_30_days > 0 else 0

        # Average completion time for last 30 days
        avg_time_30_days_tasks = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.COMPLETED,
            Task.created_at >= thirty_days_ago,
            Task.updated_at.isnot(None)
        ).all()

        avg_time_30_days_hours = 0
        if avg_time_30_days_tasks:
            total_hours = sum((t.updated_at - t.created_at).total_seconds() / 3600 for t in avg_time_30_days_tasks)
            avg_time_30_days_hours = total_hours / len(avg_time_30_days_tasks)

        # Calculate trends
        created_trend = ((created_7_days / 7) - (created_30_days / 30)) / (
                    created_30_days / 30) * 100 if created_30_days > 0 else 0
        completed_trend = ((completed_7_days / 7) - (completed_30_days / 30)) / (
                    completed_30_days / 30) * 100 if completed_30_days > 0 else 0
        completion_rate_trend = completion_rate_7_days - completion_rate_30_days
        avg_time_trend = (
                                     avg_time_7_days_hours - avg_time_30_days_hours) / avg_time_30_days_hours * 100 if avg_time_30_days_hours > 0 else 0

        task_metrics = {
            'created_7_days': created_7_days,
            'completed_7_days': completed_7_days,
            'completion_rate_7_days': round(completion_rate_7_days),
            'avg_time_7_days': round(avg_time_7_days_hours, 1),

            'created_30_days': created_30_days,
            'completed_30_days': completed_30_days,
            'completion_rate_30_days': round(completion_rate_30_days),
            'avg_time_30_days': round(avg_time_30_days_hours, 1),

            'created_trend': round(created_trend),
            'completed_trend': round(completed_trend),
            'completion_rate_trend': round(completion_rate_trend),
            'avg_time_trend': round(avg_time_trend)
        }

    log_action(ActionType.VIEW, f"Viewed {report_type} statistics", db)

    return render_template(
        'operator/statistics.html',
        title='Statistics',
        period=period,
        report_type=report_type,
        driver_id=driver_id,
        drivers=drivers,
        kpi=kpi,
        start_date=start_date.strftime('%Y-%m-%d') if hasattr(start_date, 'strftime') else '',
        end_date=end_date.strftime('%Y-%m-%d') if hasattr(end_date, 'strftime') else '',
        start_date_display=start_date.strftime('%b %d, %Y') if hasattr(start_date, 'strftime') else '',
        end_date_display=end_date.strftime('%b %d, %Y') if hasattr(end_date, 'strftime') else '',
        # Driver report specific
        driver_stats=driver_stats if report_type == 'drivers' else [],
        task_stats=task_stats,
        on_time_stats=on_time_stats if report_type == 'drivers' else {},
        # Route report specific
        route_stats=route_stats if report_type == 'routes' else {},
        daily_routes=daily_routes if report_type == 'routes' else [],
        route_time_stats=route_time_stats if report_type == 'routes' else [],
        recent_routes=recent_routes if report_type == 'routes' else [],
        # Task report specific
        daily_tasks=daily_tasks if report_type == 'tasks' else [],
        task_time_stats=task_time_stats if report_type == 'tasks' else {},
        task_distribution=task_distribution if report_type == 'tasks' else [],
        task_metrics=task_metrics if report_type == 'tasks' else {}
    )


@operator.route('/download-report')
@login_required
@role_required(UserRole.OPERATOR.value)
def download_report():
    """
    Download statistics report as CSV
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    report_format = request.args.get('format', 'csv')
    report_type = request.args.get('report_type', 'drivers')

    # Get date range from request, default to last 30 days
    period = request.args.get('period', '30')
    driver_id = request.args.get('driver_id', None, type=int)

    # Handle custom date range
    if period == 'custom':
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if not start_date or not end_date:
            # Default to last 30 days if dates not provided
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=30)
        else:
            # Parse dates from string
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                # Set end_date to end of day
                end_date = end_date.replace(hour=23, minute=59, second=59)
            except ValueError:
                end_date = datetime.utcnow()
                start_date = end_date - timedelta(days=30)
    else:
        # Calculate date range based on period
        days = int(period)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

    # Get driver IDs for this operator
    operator_driver_ids = [d.id for d in Driver.query.filter_by(operator_id=current_user.operator.id).all()]

    # Only CSV format is implemented
    if report_format != 'csv':
        flash('Only CSV format is currently supported.', 'warning')
        return redirect(url_for('operator.statistics', period=period, report_type=report_type, driver_id=driver_id))

    # Create CSV file
    import io
    import csv
    from flask import send_file

    output = io.StringIO()
    writer = csv.writer(output)

    # Write header with report info
    writer.writerow(['Report Type', report_type.capitalize()])
    writer.writerow(['Period', f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"])
    writer.writerow(['Generated', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')])
    writer.writerow(['Generated By', f"{current_user.first_name} {current_user.last_name}"])
    writer.writerow([])

    # Write report based on type
    if report_type == 'drivers':
        writer.writerow(['Driver Performance Report'])
        writer.writerow([])

        # Write headers
        writer.writerow(['Driver Name', 'Total Tasks', 'Completed Tasks', 'Completion Rate',
                         'Avg Completion Time (min)', 'Total Distance (km)', 'On-Time Rate'])

        # Write data for each driver
        for driver in Driver.query.filter_by(operator_id=current_user.operator.id).all():
            if not driver.user:
                continue

            # Get completed tasks
            driver_tasks = Task.query.filter(
                Task.assignee_id == driver.id,
                Task.created_at.between(start_date, end_date)
            ).all()

            total_tasks_count = len(driver_tasks)
            completed_tasks_count = sum(1 for t in driver_tasks if t.status == TaskStatus.COMPLETED)

            # Get completed routes
            driver_routes = Route.query.filter(
                Route.driver_id == driver.id,
                Route.created_at.between(start_date, end_date)
            ).all()

            # Calculate completion rate
            completion_rate = (completed_tasks_count / total_tasks_count * 100) if total_tasks_count > 0 else 0

            # Calculate on-time rate (for demo, use a random value)
            import random
            on_time_rate = random.randint(60, 100)

            # Calculate total distance
            driver_distance = sum(r.distance or 0 for r in driver_routes if r.status == RouteStatus.COMPLETED)

            # Calculate average completion time
            completion_times = []
            for task in driver_tasks:
                if task.status == TaskStatus.COMPLETED and task.created_at and task.updated_at:
                    duration = (task.updated_at - task.created_at).total_seconds() / 60  # in minutes
                    completion_times.append(duration)

            avg_completion_time = sum(completion_times) / len(completion_times) if completion_times else 0

            writer.writerow([
                f"{driver.user.first_name} {driver.user.last_name}",
                total_tasks_count,
                completed_tasks_count,
                f"{round(completion_rate, 1)}%",
                round(avg_completion_time),
                round(driver_distance, 1),
                f"{on_time_rate}%"
            ])

    elif report_type == 'routes':
        writer.writerow(['Route Analysis Report'])
        writer.writerow([])

        # Write route status summary
        writer.writerow(['Route Status Summary'])
        writer.writerow(['Status', 'Count', 'Percentage'])

        # Get route stats
        planned = Route.query.filter(
            Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
            Route.status == RouteStatus.PLANNED,
            Route.created_at.between(start_date, end_date)
        ).count()

        in_progress = Route.query.filter(
            Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
            Route.status == RouteStatus.IN_PROGRESS,
            Route.created_at.between(start_date, end_date)
        ).count()

        completed = Route.query.filter(
            Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
            Route.status == RouteStatus.COMPLETED,
            Route.created_at.between(start_date, end_date)
        ).count()

        cancelled = Route.query.filter(
            Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
            Route.status == RouteStatus.CANCELLED,
            Route.created_at.between(start_date, end_date)
        ).count()

        total_routes = planned + in_progress + completed + cancelled

        writer.writerow(['Planned', planned, f"{(planned / total_routes * 100) if total_routes > 0 else 0:.1f}%"])
        writer.writerow(
            ['In Progress', in_progress, f"{(in_progress / total_routes * 100) if total_routes > 0 else 0:.1f}%"])
        writer.writerow(['Completed', completed, f"{(completed / total_routes * 100) if total_routes > 0 else 0:.1f}%"])
        writer.writerow(['Cancelled', cancelled, f"{(cancelled / total_routes * 100) if total_routes > 0 else 0:.1f}%"])
        writer.writerow(['Total', total_routes, '100.0%'])

        writer.writerow([])
        writer.writerow(['Recent Routes'])
        writer.writerow(['Start Point', 'End Point', 'Driver', 'Status', 'Distance (km)', 'Duration (min)'])

        # Get recent routes
        recent_routes = Route.query.filter(
            Route.driver_id.in_(operator_driver_ids) if operator_driver_ids else False,
            Route.created_at.between(start_date, end_date)
        ).order_by(Route.created_at.desc()).limit(20).all()

        for route in recent_routes:
            driver_name = f"{route.driver.user.first_name} {route.driver.user.last_name}" if route.driver and route.driver.user else "Unassigned"

            duration = "N/A"
            if route.actual_start_time and route.end_time:
                duration_min = int((route.end_time - route.actual_start_time).total_seconds() / 60)
                duration = f"{duration_min}"

            writer.writerow([
                route.start_point,
                route.end_point,
                driver_name,
                route.status.value.replace('_', ' ').title(),
                f"{route.distance:.1f}" if route.distance else "N/A",
                duration
            ])

    elif report_type == 'tasks':
        writer.writerow(['Task Metrics Report'])
        writer.writerow([])

        # Write task status summary
        writer.writerow(['Task Status Summary'])
        writer.writerow(['Status', 'Count', 'Percentage'])

        # Get task stats
        new_tasks = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.NEW,
            Task.created_at.between(start_date, end_date)
        ).count()

        in_progress = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.IN_PROGRESS,
            Task.created_at.between(start_date, end_date)
        ).count()

        on_hold = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.ON_HOLD,
            Task.created_at.between(start_date, end_date)
        ).count()

        completed = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.COMPLETED,
            Task.created_at.between(start_date, end_date)
        ).count()

        cancelled = Task.query.filter(
            Task.creator_id == current_user.id,
            Task.status == TaskStatus.CANCELLED,
            Task.created_at.between(start_date, end_date)
        ).count()

        total_tasks = new_tasks + in_progress + on_hold + completed + cancelled

        writer.writerow(['New', new_tasks, f"{(new_tasks / total_tasks * 100) if total_tasks > 0 else 0:.1f}%"])
        writer.writerow(
            ['In Progress', in_progress, f"{(in_progress / total_tasks * 100) if total_tasks > 0 else 0:.1f}%"])
        writer.writerow(['On Hold', on_hold, f"{(on_hold / total_tasks * 100) if total_tasks > 0 else 0:.1f}%"])
        writer.writerow(['Completed', completed, f"{(completed / total_tasks * 100) if total_tasks > 0 else 0:.1f}%"])
        writer.writerow(['Cancelled', cancelled, f"{(cancelled / total_tasks * 100) if total_tasks > 0 else 0:.1f}%"])
        writer.writerow(['Total', total_tasks, '100.0%'])

        writer.writerow([])
        writer.writerow(['Task Assignment By Driver'])
        writer.writerow(['Driver Name', 'Tasks Assigned', 'Percentage'])

        # Get task distribution by driver
        driver_task_counts = []

        for driver in Driver.query.filter_by(operator_id=current_user.operator.id).all():
            if not driver.user:
                continue

            task_count = Task.query.filter(
                Task.creator_id == current_user.id,
                Task.assignee_id == driver.id,
                Task.created_at.between(start_date, end_date)
            ).count()

            if task_count > 0:
                driver_task_counts.append({
                    'name': f"{driver.user.first_name} {driver.user.last_name}",
                    'count': task_count
                })

        for driver_data in driver_task_counts:
            writer.writerow([
                driver_data['name'],
                driver_data['count'],
                f"{(driver_data['count'] / total_tasks * 100) if total_tasks > 0 else 0:.1f}%"
            ])

    # Prepare file for download
    output.seek(0)

    log_action(ActionType.DOWNLOAD, f"Downloaded {report_type} report", db)

    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"{report_type}_report_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    )


@operator.route('/view-driver/<int:driver_id>')
@login_required
@role_required(UserRole.OPERATOR.value)
def view_driver(driver_id):
    """
    View detailed information about a driver
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    # Get driver
    driver = Driver.query.get_or_404(driver_id)

    # Check if driver is assigned to this operator
    if driver.operator_id != current_user.operator.id:
        flash('This driver is not assigned to you.', 'danger')
        return redirect(url_for('operator.drivers'))

    # Get active route
    active_route = Route.query.filter(
        Route.driver_id == driver.id,
        Route.status == RouteStatus.IN_PROGRESS
    ).first()

    # Get next planned route
    next_route = Route.query.filter(
        Route.driver_id == driver.id,
        Route.status == RouteStatus.PLANNED
    ).order_by(Route.start_time).first()

    # Get recent completed routes
    recent_routes = Route.query.filter(
        Route.driver_id == driver.id,
        Route.status == RouteStatus.COMPLETED
    ).order_by(Route.end_time.desc()).limit(5).all()

    # Get active task
    active_task = Task.query.filter(
        Task.assignee_id == driver.id,
        Task.status == TaskStatus.IN_PROGRESS
    ).first()

    # Get recent completed tasks
    recent_tasks = Task.query.filter(
        Task.assignee_id == driver.id,
        Task.status == TaskStatus.COMPLETED
    ).order_by(Task.updated_at.desc()).limit(5).all()

    # Get performance stats
    # Get completed tasks
    total_tasks = Task.query.filter(
        Task.assignee_id == driver.id
    ).count()

    completed_tasks = Task.query.filter(
        Task.assignee_id == driver.id,
        Task.status == TaskStatus.COMPLETED
    ).count()

    # Calculate task completion rate
    task_completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    # Get completed routes
    total_routes = Route.query.filter(
        Route.driver_id == driver.id
    ).count()

    completed_routes = Route.query.filter(
        Route.driver_id == driver.id,
        Route.status == RouteStatus.COMPLETED
    ).count()

    # Calculate route completion rate
    route_completion_rate = (completed_routes / total_routes * 100) if total_routes > 0 else 0

    # Calculate total distance driven
    total_distance = db.session.query(func.sum(Route.distance)).filter(
        Route.driver_id == driver.id,
        Route.status == RouteStatus.COMPLETED
    ).scalar() or 0

    # Calculate on-time percentage (example - in real app, compare actual completion time with deadline)
    import random
    on_time_percentage = random.randint(75, 95)

    # Get activity logs
    activity_logs = Log.query.filter(
        Log.user_id == driver.id
    ).order_by(Log.timestamp.desc()).limit(10).all()

    log_action(ActionType.VIEW, f"Viewed driver {driver.user.first_name} {driver.user.last_name}", db)

    return render_template(
        'operator/view_driver.html',
        title=f'Driver: {driver.user.first_name} {driver.user.last_name}',
        driver=driver,
        active_route=active_route,
        next_route=next_route,
        recent_routes=recent_routes,
        active_task=active_task,
        recent_tasks=recent_tasks,
        task_completion_rate=round(task_completion_rate),
        route_completion_rate=round(route_completion_rate),
        total_distance=round(total_distance, 1),
        on_time_percentage=on_time_percentage,
        activity_logs=activity_logs,
        now = datetime.utcnow(),
    )


@operator.route('/edit-driver/<int:driver_id>', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.OPERATOR.value)
def edit_driver(driver_id):
    """
    Edit driver information
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    # Get driver
    driver = Driver.query.get_or_404(driver_id)

    # Check if driver is assigned to this operator
    if driver.operator_id != current_user.operator.id:
        flash('This driver is not assigned to you.', 'danger')
        return redirect(url_for('operator.drivers'))

    # Create form
    from forms import EditUserForm

    form = EditUserForm(
        original_username=driver.user.username,
        original_email=driver.user.email
    )

    if request.method == 'GET':
        form.username.data = driver.user.username
        form.email.data = driver.user.email
        form.first_name.data = driver.user.first_name
        form.last_name.data = driver.user.last_name
        form.phone.data = driver.user.phone
        form.is_active.data = driver.user.is_active

    if form.validate_on_submit():
        try:
            # Update user details
            driver.user.username = form.username.data
            driver.user.email = form.email.data
            driver.user.first_name = form.first_name.data
            driver.user.last_name = form.last_name.data
            driver.user.phone = form.phone.data
            driver.user.is_active = form.is_active.data

            # Update driver details
            driver.license_number = request.form.get('license_number', driver.license_number)
            driver.vehicle_info = request.form.get('vehicle_info', driver.vehicle_info)

            # Handle profile image if provided
            if form.profile_image.data:
                from utils import save_profile_image
                profile_image = save_profile_image(form.profile_image.data)
                if profile_image:
                    driver.user.profile_image = profile_image

            # Handle password reset if requested
            if request.form.get('reset_password') == 'on':
                # Generate random password
                import random
                import string
                temp_password = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(10))
                driver.user.set_password(temp_password)

                # Send notification to driver
                message = Message(
                    content=f"Your password has been reset. Your new temporary password is: {temp_password}",
                    sender_id=current_user.id,
                    recipient_id=driver.id,
                    sent_at=datetime.utcnow(),
                    is_read=False,
                    company_id=current_user.operator.company_id
                )
                db.session.add(message)

                flash(f'Password has been reset to: {temp_password}. Please share this with the driver securely.',
                      'success')

            db.session.commit()
            log_action(ActionType.UPDATE, f"Updated driver {driver.user.username}", db)

            flash(f'Driver {driver.user.first_name} {driver.user.last_name} updated successfully!', 'success')
            return redirect(url_for('operator.view_driver', driver_id=driver.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating driver: {str(e)}', 'danger')

    return render_template(
        'operator/edit_driver.html',
        title='Edit Driver',
        form=form,
        driver=driver
    )


@operator.route('/request-driver', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.OPERATOR.value)
def request_driver():
    """
    Request a new driver to be added
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    if request.method == 'POST':
        # Process the request
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        license_number = request.form.get('license_number')
        vehicle_info = request.form.get('vehicle_info')
        notes = request.form.get('notes')

        if not first_name or not last_name or not email or not license_number:
            flash('Please fill in all required fields.', 'danger')
            return redirect(url_for('operator.request_driver'))

        try:
            # Find manager to send request to
            manager_id = current_user.operator.manager_id

            if not manager_id:
                # If no manager, send to company owner
                company_owner = db.session.query(User).join(
                    User.company_owner
                ).filter(
                    User.company_owner.has(company_id=current_user.operator.company_id)
                ).first()

                if company_owner:
                    recipient_id = company_owner.id
                else:
                    # If no company owner, no one to send request to
                    flash('No manager or company owner found to send request to.', 'danger')
                    return redirect(url_for('operator.drivers'))
            else:
                recipient_id = manager_id

            # Create message with request details
            message_content = f"""Driver Account Request:

First Name: {first_name}
Last Name: {last_name}
Email: {email}
Phone: {phone or 'Not provided'}
License Number: {license_number}
Vehicle Info: {vehicle_info or 'Not provided'}

Additional Notes:
{notes or 'None'}

Requested by: {current_user.first_name} {current_user.last_name}
"""

            message = Message(
                content=message_content,
                sender_id=current_user.id,
                recipient_id=recipient_id,
                sent_at=datetime.utcnow(),
                is_read=False,
                company_id=current_user.operator.company_id
            )

            db.session.add(message)
            db.session.commit()

            log_action(ActionType.CREATE, "Requested new driver account", db)
            flash('Your driver request has been submitted.', 'success')

            return redirect(url_for('operator.drivers'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error submitting request: {str(e)}', 'danger')

    return render_template(
        'operator/request_driver.html',
        title='Request New Driver'
    )


@operator.route('/upload_document', methods=['GET', 'POST'])
@login_required
@role_required(UserRole.OPERATOR.value)
def upload_document():
    """
    Upload document page for operators
    """
    if not current_user.operator or not current_user.operator.company_id:
        flash('You are not associated with a company.', 'danger')
        return redirect(url_for('main.index'))

    # Create form
    form = DocumentUploadForm()

    # Get task ID from query parameter
    task_id = request.args.get('task_id', None, type=int)

    # Get company ID
    company_id = current_user.operator.company_id

    # Populate task choices
    tasks = Task.query.filter_by(
        creator_id=current_user.id,
        company_id=company_id
    ).all()

    # Set task choices
    task_choices = [(0, 'General Document (No Task)')]
    task_choices.extend([(t.id, f'{t.id} - {t.title}') for t in tasks])

    # Create a custom form field for task selection
    class DocumentUploadFormWithTask(DocumentUploadForm):
        task_id = SelectField('Associated Task', choices=task_choices, coerce=int, validators=[Optional()])

    # Use the extended form
    form = DocumentUploadFormWithTask()

    # Set selected task if provided
    if task_id and task_id in [t[0] for t in task_choices]:
        form.task_id.data = task_id

    if form.validate_on_submit():
        try:
            # Process file upload
            file = form.document.data

            # Determine task_id (if any)
            selected_task_id = form.task_id.data if form.task_id.data != 0 else None

            # Save document file and get file info
            file_path, file_type, file_size = save_document(file, selected_task_id, company_id)

            if not file_path:
                flash('Error saving document. Please check file type and size.', 'danger')
                return redirect(url_for('operator.upload_document'))

            # Create document record
            document = Document(
                title=form.title.data,
                file_path=file_path,
                file_type=file_type,
                size=file_size,
                uploaded_at=datetime.utcnow(),
                uploader_id=current_user.id,
                task_id=selected_task_id,
                company_id=company_id,
                document_category='other'  # Default category
            )

            db.session.add(document)
            db.session.commit()

            log_action(ActionType.UPLOAD, f"Uploaded document: {form.title.data}", db)
            flash('Document uploaded successfully!', 'success')

            # Redirect back to document list
            return redirect(url_for('operator.documents'))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error uploading document: {str(e)}")
            flash(f'Error uploading document: {str(e)}', 'danger')

    log_action(ActionType.VIEW, "Viewed document upload page", db)

    return render_template(
        'operator/upload_document.html',
        title='Upload Document',
        form=form
    )