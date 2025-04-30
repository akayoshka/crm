from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from sqlalchemy import func, distinct, case, extract, or_

from app import db
from models import (
    User, UserRole, Company, Task, TaskStatus, Route, RouteStatus,
    Driver, Operator, Manager, CompanyOwner, Document, Message,
    Statistics, Log, ActionType
)
from utils import role_required, log_action
import io
import csv
import random  # For demo data

statistics = Blueprint('statistics', __name__, url_prefix='/statistics')


@statistics.route('/dashboard')
@login_required
@role_required(["ADMIN", "COMPANY_OWNER", "MANAGER"])
def dashboard():
    """
    Show statistics dashboard
    """
    # Get date range from request, default to last 30 days
    period = request.args.get('period', '30')

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
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
            # Set end_date to end of day
            end_date = end_date.replace(hour=23, minute=59, second=59)
    else:
        # Calculate date range based on period
        days = int(period)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id

    # For admins, they can filter by company
    if current_user.role == UserRole.ADMIN:
        company_id = request.args.get('company_id', None, type=int)

    # Get statistics for the dashboard
    # In a real application, this would query the database for actual stats
    # Here we'll generate some sample data for demonstration

    # Task statistics
    task_query = Task.query

    if company_id:
        task_query = task_query.filter(Task.company_id == company_id)

    task_query = task_query.filter(Task.created_at.between(start_date, end_date))

    task_count = task_query.count()
    new_tasks = task_query.filter(Task.status == TaskStatus.NEW).count()
    in_progress_tasks = task_query.filter(Task.status == TaskStatus.IN_PROGRESS).count()
    completed_tasks = task_query.filter(Task.status == TaskStatus.COMPLETED).count()
    cancelled_tasks = task_query.filter(Task.status == TaskStatus.CANCELLED).count()

    # Route statistics
    route_query = Route.query

    if company_id:
        route_query = route_query.filter(Route.company_id == company_id)

    route_query = route_query.filter(Route.start_time.between(start_date, end_date))

    route_count = route_query.count()

    # Calculate total distance of completed routes
    total_distance = db.session.query(func.sum(Route.distance)).filter(
        Route.status == RouteStatus.COMPLETED,
        Route.start_time.between(start_date, end_date)
    )

    if company_id:
        total_distance = total_distance.filter(Route.company_id == company_id)

    total_distance = total_distance.scalar() or 0

    # Calculate task completion rate
    if task_count > 0:
        completion_rate = (completed_tasks / task_count) * 100
    else:
        completion_rate = 0

    # Generate sample task status distribution for the chart
    task_statuses = {
        'new': new_tasks,
        'in_progress': in_progress_tasks,
        'completed': completed_tasks,
        'cancelled': cancelled_tasks,
        'on_hold': task_count - (new_tasks + in_progress_tasks + completed_tasks + cancelled_tasks)
    }

    # Get top performing drivers
    top_drivers = []

    drivers_query = Driver.query
    if company_id:
        drivers_query = drivers_query.filter(Driver.company_id == company_id)

    drivers = drivers_query.limit(5).all()

    for driver in drivers:
        # In a real app, this would use actual metrics
        routes_completed = route_query.filter(
            Route.driver_id == driver.id,
            Route.status == RouteStatus.COMPLETED
        ).count()

        # Calculate metrics for this driver
        driver_distance = db.session.query(func.sum(Route.distance)).filter(
            Route.status == RouteStatus.COMPLETED,
            Route.driver_id == driver.id,
            Route.start_time.between(start_date, end_date)
        ).scalar() or 0

        # Calculate average completion time
        avg_time_query = db.session.query(
            func.avg(
                func.extract('epoch', Route.end_time) -
                func.extract('epoch', Route.start_time)
            ) / 60  # Convert to minutes
        ).filter(
            Route.status == RouteStatus.COMPLETED,
            Route.driver_id == driver.id,
            Route.start_time.isnot(None),
            Route.end_time.isnot(None),
            Route.start_time.between(start_date, end_date)
        )

        avg_time = avg_time_query.scalar() or 0

        # In a real app, on-time percentage would be calculated from actual data
        # For demo, generate a random score between 80-100%
        on_time_percent = random.randint(80, 100)

        # Performance score based on completion rate and on-time percentage
        performance_score = (routes_completed / (routes_completed + 2)) * 80 + (
                    on_time_percent / 100) * 20 if routes_completed > 0 else 50

        # Add driver to list
        if driver.user:
            top_drivers.append({
                'name': f"{driver.user.first_name} {driver.user.last_name}",
                'routes_completed': routes_completed,
                'total_distance': driver_distance,
                'avg_time': round(avg_time),
                'on_time_percent': on_time_percent,
                'performance_score': round(performance_score)
            })

    # Sort by performance score (descending)
    top_drivers.sort(key=lambda x: x['performance_score'], reverse=True)

    # Combine all stats into a single object
    stats = {
        'task_count': task_count,
        'route_count': route_count,
        'total_distance': total_distance,
        'completion_rate': completion_rate,
        'task_statuses': task_statuses
    }

    log_action(ActionType.VIEW, "Viewed statistics dashboard", db)

    return render_template(
        'statistics/dashboard.html',
        title='Statistics Dashboard',
        stats=stats,
        top_drivers=top_drivers,
        period=period,
        start_date=start_date.strftime('%Y-%m-%d') if start_date else '',
        end_date=end_date.strftime('%Y-%m-%d') if end_date else ''
    )


@statistics.route('/company')
@login_required
@role_required(["ADMIN", "COMPANY_OWNER", "MANAGER"])
def company():
    """
    Show company statistics
    """
    # Get date range from request, default to last 30 days
    period = request.args.get('period', '30')

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
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
            # Set end_date to end of day
            end_date = end_date.replace(hour=23, minute=59, second=59)
    else:
        # Calculate date range based on period
        days = int(period)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id

    # For admins, they can filter by company
    if current_user.role == UserRole.ADMIN:
        company_id = request.args.get('company_id', None, type=int)

        # Get all companies for admin dropdown
        companies = Company.query.all()
    else:
        # Non-admins only see their own company
        companies = []
        if company_id:
            companies = [Company.query.get(company_id)]

    # Get company statistics
    # User counts by role
    managers_count = Manager.query.filter_by(company_id=company_id).count() if company_id else 0
    operators_count = Operator.query.filter_by(company_id=company_id).count() if company_id else 0
    drivers_count = Driver.query.filter_by(company_id=company_id).count() if company_id else 0

    # For admin - all users, for company owner - only company employees
    if current_user.role == UserRole.ADMIN:
        user_count = User.query.count()
    else:
        # Sum of all employees in the company
        user_count = managers_count + operators_count + drivers_count

    # Calculate average task completion time
    avg_completion_time = 0
    if company_id:
        avg_time_query = db.session.query(
            func.avg(
                func.extract('epoch', Route.end_time) -
                func.extract('epoch', Route.start_time)
            ) / 3600  # Convert to hours
        ).filter(
            Route.company_id == company_id,
            Route.status == RouteStatus.COMPLETED,
            Route.start_time.isnot(None),
            Route.end_time.isnot(None),
            Route.start_time.between(start_date, end_date)
        )

        avg_completion_time = round(avg_time_query.scalar() or 0, 1)

    # Get real task and route data for company efficiency score
    if company_id:
        # Get task statistics
        task_query = Task.query.filter_by(company_id=company_id)
        total_tasks = task_query.count()
        completed_tasks = task_query.filter_by(status=TaskStatus.COMPLETED).count()

        # Calculate efficiency based on completion rate and average completion time
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        efficiency_score = int(completion_rate)
    else:
        efficiency_score = 0

    # Combine all stats into a single object
    stats = {
        'user_count': user_count,
        'managers': managers_count,
        'operators': operators_count,
        'drivers': drivers_count,
        'efficiency_score': efficiency_score,
        'avg_completion_time': avg_completion_time
    }

    log_action(ActionType.VIEW, "Viewed company statistics", db)

    return render_template(
        'statistics/company.html',
        title='Company Statistics',
        stats=stats,
        departments=[],  # Remove fake department data
        companies=companies,
        company_id=company_id,
        period=period,
        start_date=start_date.strftime('%Y-%m-%d') if start_date else '',
        end_date=end_date.strftime('%Y-%m-%d') if end_date else ''
    )


@statistics.route('/routes')
@login_required
@role_required(["ADMIN", "COMPANY_OWNER", "MANAGER", "OPERATOR"])
def routes():
    """
    Show routes statistics
    """
    # Get date range from request, default to last 30 days
    period = request.args.get('period', '30')

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
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
            # Set end_date to end of day
            end_date = end_date.replace(hour=23, minute=59, second=59)
    else:
        # Calculate date range based on period
        days = int(period)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id
    elif current_user.role == UserRole.OPERATOR and current_user.operator:
        company_id = current_user.operator.company_id

    # For admins, they can filter by company
    if current_user.role == UserRole.ADMIN:
        company_id = request.args.get('company_id', None, type=int)

    # Get route statistics
    route_query = Route.query

    if company_id:
        route_query = route_query.filter(Route.company_id == company_id)

    route_query = route_query.filter(Route.start_time.between(start_date, end_date))

    # Basic counts
    total_routes = route_query.count()
    completed_routes = route_query.filter(Route.status == RouteStatus.COMPLETED).count()
    in_progress_routes = route_query.filter(Route.status == RouteStatus.IN_PROGRESS).count()
    planned_routes = route_query.filter(Route.status == RouteStatus.PLANNED).count()
    cancelled_routes = route_query.filter(Route.status == RouteStatus.CANCELLED).count()

    # Calculate total distance
    total_distance = db.session.query(func.sum(Route.distance)).filter(
        Route.start_time.between(start_date, end_date)
    )

    if company_id:
        total_distance = total_distance.filter(Route.company_id == company_id)

    total_distance = total_distance.scalar() or 0

    # Calculate completion rate
    completion_rate = (completed_routes / total_routes * 100) if total_routes > 0 else 0

    # Calculate average time per route
    avg_time_query = db.session.query(
        func.avg(
            func.extract('epoch', Route.end_time) -
            func.extract('epoch', Route.start_time)
        ) / 60  # Convert to minutes
    ).filter(
        Route.status == RouteStatus.COMPLETED,
        Route.start_time.isnot(None),
        Route.end_time.isnot(None),
        Route.start_time.between(start_date, end_date)
    )

    if company_id:
        avg_time_query = avg_time_query.filter(Route.company_id == company_id)

    avg_time = avg_time_query.scalar() or 0

    # Get top routes by distance
    top_routes = route_query.filter(
        Route.distance.isnot(None)
    ).order_by(Route.distance.desc()).limit(5).all()

    # Get most active drivers
    active_drivers = db.session.query(
        Driver.id,
        User.first_name,
        User.last_name,
        func.count(Route.id).label('route_count')
    ).join(
        User, Driver.id == User.id
    ).join(
        Route, Driver.id == Route.driver_id
    ).filter(
        Route.start_time.between(start_date, end_date)
    )

    if company_id:
        active_drivers = active_drivers.filter(Driver.company_id == company_id)

    active_drivers = active_drivers.group_by(
        Driver.id, User.first_name, User.last_name
    ).order_by(
        func.count(Route.id).desc()
    ).limit(5).all()

    # Combine all stats into a single object
    stats = {
        'total_routes': total_routes,
        'completed_routes': completed_routes,
        'in_progress_routes': in_progress_routes,
        'planned_routes': planned_routes,
        'cancelled_routes': cancelled_routes,
        'total_distance': total_distance,
        'completion_rate': round(completion_rate, 1),
        'avg_time': round(avg_time)
    }

    # Generate sample data for route distribution by day of week
    weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    route_distribution = []

    for weekday in range(7):
        # In a real app, this would use database aggregation
        # For demo, generate random counts
        route_distribution.append({
            'day': weekdays[weekday],
            'count': random.randint(5, 20),
            'completed': random.randint(3, 18)
        })

    log_action(ActionType.VIEW, "Viewed routes statistics", db)

    return render_template(
        'statistics/routes.html',
        title='Routes Statistics',
        stats=stats,
        top_routes=top_routes,
        active_drivers=active_drivers,
        route_distribution=route_distribution,
        period=period,
        start_date=start_date.strftime('%Y-%m-%d') if start_date else '',
        end_date=end_date.strftime('%Y-%m-%d') if end_date else ''
    )


@statistics.route('/users')
@login_required
@role_required(["ADMIN", "COMPANY_OWNER", "MANAGER"])
def users():
    """
    Show user performance statistics
    """
    # Get date range from request, default to last 30 days
    period = request.args.get('period', '30')

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
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
            end_date = datetime.strptime(end_date, '%Y-%m-%d')
            # Set end_date to end of day
            end_date = end_date.replace(hour=23, minute=59, second=59)
    else:
        # Calculate date range based on period
        days = int(period)
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id

    # For admins, they can filter by company
    if current_user.role == UserRole.ADMIN:
        company_id = request.args.get('company_id', None, type=int)

    # Default user role to filter by
    role_filter = request.args.get('role', 'all')

    # Get user statistics
    users_query = User.query

    # Filter by company
    if company_id:
        if role_filter == 'driver':
            users_query = users_query.join(
                Driver
            ).filter(
                Driver.company_id == company_id
            )
        elif role_filter == 'operator':
            users_query = users_query.join(
                Operator
            ).filter(
                Operator.company_id == company_id
            )
        elif role_filter == 'manager':
            users_query = users_query.join(
                Manager
            ).filter(
                Manager.company_id == company_id
            )
        else:
            # All users in this company
            users_query = users_query.join(
                CompanyOwner, full=True
            ).join(
                Manager, full=True
            ).join(
                Operator, full=True
            ).join(
                Driver, full=True
            ).filter(
                or_(
                    CompanyOwner.company_id == company_id,
                    Manager.company_id == company_id,
                    Operator.company_id == company_id,
                    Driver.company_id == company_id
                )
            )

    # If current user is manager, show only their operators and their drivers
    if current_user.role == UserRole.MANAGER and current_user.manager:
        # Get operators managed by this manager
        managed_operator_ids = [op.id for op in Operator.query.filter_by(manager_id=current_user.manager.id).all()]

        # Get drivers managed by these operators
        managed_driver_ids = []
        if managed_operator_ids:
            managed_driver_ids = [d.id for d in Driver.query.filter(Driver.operator_id.in_(managed_operator_ids)).all()]

        # Filter users to show only the manager, their operators and their drivers
        users_query = users_query.filter(
            or_(
                User.id == current_user.id,  # The manager
                User.id.in_(managed_operator_ids),  # Manager's operators
                User.id.in_(managed_driver_ids)  # Drivers managed by operator
            )
        )

    # Filter by role
    if role_filter != 'all':
        users_query = users_query.filter(User.role == UserRole(role_filter.upper()))
    # Get users
    users = users_query.all()

    # Get activity statistics for each user
    user_stats = []

    for user in users:
        # Get task counts
        task_count = 0
        completed_task_count = 0

        if user.role == UserRole.MANAGER or user.role == UserRole.OPERATOR:
            # Tasks created by this user
            task_query = Task.query.filter(
                Task.creator_id == user.id,
                Task.created_at.between(start_date, end_date)
            )
            task_count = task_query.count()
            completed_task_count = task_query.filter(Task.status == TaskStatus.COMPLETED).count()

        # Get route counts for drivers
        route_count = 0
        completed_route_count = 0
        total_distance = 0

        if user.role == UserRole.DRIVER and user.driver:
            route_query = Route.query.filter(
                Route.driver_id == user.driver.id,
                Route.start_time.between(start_date, end_date)
            )
            route_count = route_query.count()
            completed_route_count = route_query.filter(Route.status == RouteStatus.COMPLETED).count()

            # Calculate total distance
            total_distance_query = db.session.query(func.sum(Route.distance)).filter(
                Route.driver_id == user.driver.id,
                Route.status == RouteStatus.COMPLETED,
                Route.start_time.between(start_date, end_date)
            )
            total_distance = total_distance_query.scalar() or 0

        # Get login activity
        login_count = Log.query.filter(
            Log.user_id == user.id,
            Log.action_type == ActionType.LOGIN,
            Log.timestamp.between(start_date, end_date)
        ).count()

        # Get message counts
        message_count = Message.query.filter(
            Message.sender_id == user.id,
            Message.sent_at.between(start_date, end_date)
        ).count()

        # Calculate performance score (demo)
        performance_score = 0

        if user.role == UserRole.DRIVER:
            # For drivers, base on route completion
            if route_count > 0:
                performance_score = (completed_route_count / route_count) * 100
            else:
                performance_score = 0
        elif user.role in [UserRole.MANAGER, UserRole.OPERATOR]:
            # For managers and operators, base on task completion
            if task_count > 0:
                performance_score = (completed_task_count / task_count) * 100
            else:
                performance_score = 0
        else:
            # For other roles, use activity metrics
            performance_score = min((login_count + message_count) * 5, 100)

        # Add user stats to list
        user_stats.append({
            'id': user.id,
            'name': f"{user.first_name} {user.last_name}",
            'username': user.username,
            'email': user.email,
            'role': user.role.value,
            'task_count': task_count,
            'completed_task_count': completed_task_count,
            'route_count': route_count,
            'completed_route_count': completed_route_count,
            'total_distance': total_distance,
            'login_count': login_count,
            'message_count': message_count,
            'performance_score': round(performance_score)
        })

    # Sort by performance score (descending)
    user_stats.sort(key=lambda x: x['performance_score'], reverse=True)

    log_action(ActionType.VIEW, "Viewed user statistics", db)

    return render_template(
        'statistics/users.html',
        title='User Statistics',
        user_stats=user_stats,
        role_filter=role_filter,
        period=period,
        start_date=start_date.strftime('%Y-%m-%d') if start_date else '',
        end_date=end_date.strftime('%Y-%m-%d') if end_date else ''
    )


@statistics.route('/reports')
@login_required
@role_required(["ADMIN", "COMPANY_OWNER", "MANAGER"])
def reports():
    """
    Show available reports
    """
    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id

    # For admins, they can filter by company
    if current_user.role == UserRole.ADMIN:
        company_id = request.args.get('company_id', None, type=int)

        # Get all companies for admin dropdown
        companies = Company.query.all()
    else:
        # Non-admins only see their own company
        companies = []
        if company_id:
            companies = [Company.query.get(company_id)]

    # Define available reports
    reports = [
        {
            'id': 'tasks_summary',
            'name': 'Tasks Summary',
            'description': 'Summary of all tasks and their statuses',
            'formats': ['csv', 'excel', 'pdf']
        },
        {
            'id': 'routes_performance',
            'name': 'Routes Performance',
            'description': 'Analysis of route completion times and distances',
            'formats': ['csv', 'excel', 'pdf']
        },
        {
            'id': 'driver_performance',
            'name': 'Driver Performance',
            'description': 'Performance metrics for all drivers',
            'formats': ['csv', 'excel', 'pdf']
        },
        {
            'id': 'company_overview',
            'name': 'Company Overview',
            'description': 'Overview of company operations and efficiency',
            'formats': ['excel', 'pdf']
        }
    ]

    log_action(ActionType.VIEW, "Viewed reports page", db)

    return render_template(
        'statistics/reports.html',
        title='Reports',
        reports=reports,
        companies=companies,
        company_id=company_id
    )


@statistics.route('/download_report/<report_id>')
@login_required
@role_required(["ADMIN", "COMPANY_OWNER", "MANAGER"])
def download_report(report_id):
    """
    Download a specific report
    """
    # Get parameters
    format_type = request.args.get('format', 'csv')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # Get company ID based on user role
    company_id = None
    if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
        company_id = current_user.company_owner.company_id
    elif current_user.role == UserRole.MANAGER and current_user.manager:
        company_id = current_user.manager.company_id

    # For admins, they can filter by company
    if current_user.role == UserRole.ADMIN:
        company_id = request.args.get('company_id', None, type=int)

    # Parse dates
    if not start_date or not end_date:
        # Default to last 30 days
        end_date_obj = datetime.utcnow()
        start_date_obj = end_date_obj - timedelta(days=30)
    else:
        # Parse dates from string
        start_date_obj = datetime.strptime(start_date, '%Y-%m-%d')
        end_date_obj = datetime.strptime(end_date, '%Y-%m-%d')
        # Set end_date to end of day
        end_date_obj = end_date_obj.replace(hour=23, minute=59, second=59)

    # Generate appropriate report based on report_id
    if report_id == 'tasks_summary':
        return _generate_tasks_report(company_id, start_date_obj, end_date_obj, format_type)
    elif report_id == 'routes_performance':
        return _generate_routes_report(company_id, start_date_obj, end_date_obj, format_type)
    elif report_id == 'driver_performance':
        return _generate_drivers_report(company_id, start_date_obj, end_date_obj, format_type)
    elif report_id == 'company_overview':
        return _generate_company_report(company_id, start_date_obj, end_date_obj, format_type)
    else:
        flash('Invalid report type.', 'danger')
        return redirect(url_for('statistics.reports'))


@statistics.route('/download_company_report')
@login_required
@role_required(["ADMIN", "COMPANY_OWNER"])
def download_company_report():
    """
    Download company report
    """
    # Get parameters
    format_type = request.args.get('format', 'csv')
    company_id = request.args.get('company_id', None, type=int)

    # Check if user has access to this company
    if not company_id:
        if current_user.role == UserRole.COMPANY_OWNER and current_user.company_owner:
            company_id = current_user.company_owner.company_id

    # For admins, they can select any company
    if current_user.role != UserRole.ADMIN and current_user.role == UserRole.COMPANY_OWNER:
        if current_user.company_owner.company_id != company_id:
            flash('You do not have access to this company.', 'danger')
            return redirect(url_for('statistics.company'))

    # Default to last 30 days
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=30)

    return _generate_company_report(company_id, start_date, end_date, format_type)


# Helper report generation functions
def _generate_tasks_report(company_id, start_date, end_date, format_type):
    """
    Generate tasks report based on specified parameters
    """
    # Get tasks for report
    task_query = Task.query.filter(Task.created_at.between(start_date, end_date))

    if company_id:
        task_query = task_query.filter(Task.company_id == company_id)

    tasks = task_query.all()

    # Log the report generation
    log_action(ActionType.DOWNLOAD, f"Downloaded tasks report ({format_type})", db)

    if format_type == 'csv':
        # Create CSV file
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(['Task ID', 'Title', 'Status', 'Created At', 'Deadline', 'Creator', 'Assignee'])

        # Write data
        for task in tasks:
            creator_name = f"{task.creator.first_name} {task.creator.last_name}" if task.creator else "Unknown"
            assignee_name = f"{task.assignee.first_name} {task.assignee.last_name}" if task.assignee else "Unassigned"

            writer.writerow([
                task.id,
                task.title,
                task.status.value,
                task.created_at.strftime('%Y-%m-%d %H:%M'),
                task.deadline.strftime('%Y-%m-%d %H:%M') if task.deadline else "None",
                creator_name,
                assignee_name
            ])

        # Prepare response
        output.seek(0)
        company_name = Company.query.get(company_id).name if company_id else "All"
        filename = f"tasks_report_{company_name}_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.csv"

        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )

    elif format_type == 'excel':
        # For excel format, we would use openpyxl or xlsxwriter
        # This is a placeholder that returns CSV instead
        flash('Excel export is not implemented yet. Returning CSV format instead.', 'warning')
        return _generate_tasks_report(company_id, start_date, end_date, 'csv')

    elif format_type == 'pdf':
        # For PDF format, we would use a library like ReportLab
        # This is a placeholder that returns CSV instead
        flash('PDF export is not implemented yet. Returning CSV format instead.', 'warning')
        return _generate_tasks_report(company_id, start_date, end_date, 'csv')

    else:
        flash('Invalid export format.', 'danger')
        return redirect(url_for('statistics.reports'))


def _generate_routes_report(company_id, start_date, end_date, format_type):
    """
    Generate routes report based on specified parameters
    """
    # Get routes for report
    route_query = Route.query.filter(Route.start_time.between(start_date, end_date))

    if company_id:
        route_query = route_query.filter(Route.company_id == company_id)

    routes = route_query.all()

    # Log the report generation
    log_action(ActionType.DOWNLOAD, f"Downloaded routes report ({format_type})", db)

    if format_type == 'csv':
        # Create CSV file
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(['Route ID', 'Start Point', 'End Point', 'Distance (km)', 'Status',
                         'Created At', 'Start Time', 'End Time', 'Duration (min)', 'Driver'])

        # Write data
        for route in routes:
            # Calculate duration if available
            duration = None
            if route.actual_start_time and route.end_time:
                duration = (route.end_time - route.actual_start_time).total_seconds() / 60  # in minutes

            driver_name = f"{route.driver.user.first_name} {route.driver.user.last_name}" if route.driver and route.driver.user else "Unassigned"

            writer.writerow([
                route.id,
                route.start_point,
                route.end_point,
                round(route.distance, 2) if route.distance else "N/A",
                route.status.value,
                route.created_at.strftime('%Y-%m-%d %H:%M'),
                route.actual_start_time.strftime('%Y-%m-%d %H:%M') if route.actual_start_time else "Not started",
                route.end_time.strftime('%Y-%m-%d %H:%M') if route.end_time else "Not completed",
                round(duration) if duration else "N/A",
                driver_name
            ])

        # Prepare response
        output.seek(0)
        company_name = Company.query.get(company_id).name if company_id else "All"
        filename = f"routes_report_{company_name}_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.csv"

        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )

    elif format_type == 'excel':
        # For excel format, we would use openpyxl or xlsxwriter
        # This is a placeholder that returns CSV instead
        flash('Excel export is not implemented yet. Returning CSV format instead.', 'warning')
        return _generate_routes_report(company_id, start_date, end_date, 'csv')

    elif format_type == 'pdf':
        # For PDF format, we would use a library like ReportLab
        # This is a placeholder that returns CSV instead
        flash('PDF export is not implemented yet. Returning CSV format instead.', 'warning')
        return _generate_routes_report(company_id, start_date, end_date, 'csv')

    else:
        flash('Invalid export format.', 'danger')
        return redirect(url_for('statistics.reports'))


def _generate_drivers_report(company_id, start_date, end_date, format_type):
    """
    Generate drivers performance report based on specified parameters
    """
    # Get drivers for report
    driver_query = Driver.query

    if company_id:
        driver_query = driver_query.filter(Driver.company_id == company_id)

    drivers = driver_query.all()

    # Log the report generation
    log_action(ActionType.DOWNLOAD, f"Downloaded drivers performance report ({format_type})", db)

    if format_type == 'csv':
        # Create CSV file
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(['Driver ID', 'Name', 'Total Routes', 'Completed Routes', 'Completion Rate',
                         'Total Distance (km)', 'Avg Duration (min)', 'On-time Delivery Rate'])

        # Write data
        for driver in drivers:
            # Get route statistics for this driver
            route_query = Route.query.filter(
                Route.driver_id == driver.id,
                Route.start_time.between(start_date, end_date)
            )

            total_routes = route_query.count()
            completed_routes = route_query.filter(Route.status == RouteStatus.COMPLETED).count()
            completion_rate = (completed_routes / total_routes * 100) if total_routes > 0 else 0

            # Calculate total distance
            total_distance_query = db.session.query(func.sum(Route.distance)).filter(
                Route.driver_id == driver.id,
                Route.status == RouteStatus.COMPLETED,
                Route.start_time.between(start_date, end_date)
            )
            total_distance = total_distance_query.scalar() or 0

            # Calculate average duration
            avg_duration_query = db.session.query(
                func.avg(
                    func.extract('epoch', Route.end_time) -
                    func.extract('epoch', Route.start_time)
                ) / 60  # Convert to minutes
            ).filter(
                Route.driver_id == driver.id,
                Route.status == RouteStatus.COMPLETED,
                Route.start_time.isnot(None),
                Route.end_time.isnot(None),
                Route.start_time.between(start_date, end_date)
            )
            avg_duration = avg_duration_query.scalar() or 0

            # Calculate on-time delivery rate
            # For demo purposes, we'll use a random value
            on_time_rate = random.randint(75, 100)

            driver_name = f"{driver.user.first_name} {driver.user.last_name}" if driver.user else f"Driver {driver.id}"

            writer.writerow([
                driver.id,
                driver_name,
                total_routes,
                completed_routes,
                f"{round(completion_rate, 2)}%",
                round(total_distance, 2),
                round(avg_duration),
                f"{on_time_rate}%"
            ])

        # Prepare response
        output.seek(0)
        company_name = Company.query.get(company_id).name if company_id else "All"
        filename = f"driver_performance_{company_name}_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.csv"

        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )

    elif format_type == 'excel':
        # For excel format, we would use openpyxl or xlsxwriter
        # This is a placeholder that returns CSV instead
        flash('Excel export is not implemented yet. Returning CSV format instead.', 'warning')
        return _generate_drivers_report(company_id, start_date, end_date, 'csv')

    elif format_type == 'pdf':
        # For PDF format, we would use a library like ReportLab
        # This is a placeholder that returns CSV instead
        flash('PDF export is not implemented yet. Returning CSV format instead.', 'warning')
        return _generate_drivers_report(company_id, start_date, end_date, 'csv')

    else:
        flash('Invalid export format.', 'danger')
        return redirect(url_for('statistics.reports'))


def _generate_company_report(company_id, start_date, end_date, format_type):
    """
    Generate company overview report
    """
    # Validate company ID
    if not company_id:
        flash('Company ID is required for company report.', 'danger')
        return redirect(url_for('statistics.reports'))

    company = Company.query.get_or_404(company_id)

    # Log the report generation
    log_action(ActionType.DOWNLOAD, f"Downloaded company report ({format_type})", db)

    if format_type == 'csv':
        # Create CSV file
        output = io.StringIO()
        writer = csv.writer(output)

        # Write company information
        writer.writerow(['Company Report'])
        writer.writerow([])
        writer.writerow(['Company Name', company.name])
        writer.writerow(['Legal Name', company.legal_name])
        writer.writerow(['Tax ID', company.tax_id])
        writer.writerow(['Date Range', f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"])
        writer.writerow([])

        # Write user statistics
        writer.writerow(['User Statistics'])
        writer.writerow(['Role', 'Count'])

        manager_count = Manager.query.filter_by(company_id=company_id).count()
        operator_count = Operator.query.filter_by(company_id=company_id).count()
        driver_count = Driver.query.filter_by(company_id=company_id).count()

        writer.writerow(['Managers', manager_count])
        writer.writerow(['Operators', operator_count])
        writer.writerow(['Drivers', driver_count])
        writer.writerow(['Total', manager_count + operator_count + driver_count])
        writer.writerow([])

        # Write task statistics
        task_query = Task.query.filter(
            Task.company_id == company_id,
            Task.created_at.between(start_date, end_date)
        )

        total_tasks = task_query.count()
        completed_tasks = task_query.filter(Task.status == TaskStatus.COMPLETED).count()
        in_progress_tasks = task_query.filter(Task.status == TaskStatus.IN_PROGRESS).count()
        new_tasks = task_query.filter(Task.status == TaskStatus.NEW).count()

        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

        writer.writerow(['Task Statistics'])
        writer.writerow(['Status', 'Count', 'Percentage'])
        writer.writerow(['Completed', completed_tasks,
                         f"{round(completed_tasks / total_tasks * 100 if total_tasks > 0 else 0, 2)}%"])
        writer.writerow(['In Progress', in_progress_tasks,
                         f"{round(in_progress_tasks / total_tasks * 100 if total_tasks > 0 else 0, 2)}%"])
        writer.writerow(['New', new_tasks, f"{round(new_tasks / total_tasks * 100 if total_tasks > 0 else 0, 2)}%"])
        writer.writerow(['Total', total_tasks, '100%'])
        writer.writerow(['Completion Rate', '', f"{round(completion_rate, 2)}%"])
        writer.writerow([])

        # Write route statistics
        route_query = Route.query.filter(
            Route.company_id == company_id,
            Route.start_time.between(start_date, end_date)
        )

        total_routes = route_query.count()
        completed_routes = route_query.filter(Route.status == RouteStatus.COMPLETED).count()
        in_progress_routes = route_query.filter(Route.status == RouteStatus.IN_PROGRESS).count()
        planned_routes = route_query.filter(Route.status == RouteStatus.PLANNED).count()

        route_completion_rate = (completed_routes / total_routes * 100) if total_routes > 0 else 0

        total_distance_query = db.session.query(func.sum(Route.distance)).filter(
            Route.company_id == company_id,
            Route.status == RouteStatus.COMPLETED,
            Route.start_time.between(start_date, end_date)
        )
        total_distance = total_distance_query.scalar() or 0

        writer.writerow(['Route Statistics'])
        writer.writerow(['Status', 'Count', 'Percentage'])
        writer.writerow(['Completed', completed_routes,
                         f"{round(completed_routes / total_routes * 100 if total_routes > 0 else 0, 2)}%"])
        writer.writerow(['In Progress', in_progress_routes,
                         f"{round(in_progress_routes / total_routes * 100 if total_routes > 0 else 0, 2)}%"])
        writer.writerow(
            ['Planned', planned_routes, f"{round(planned_routes / total_routes * 100 if total_routes > 0 else 0, 2)}%"])
        writer.writerow(['Total', total_routes, '100%'])
        writer.writerow(['Completion Rate', '', f"{round(route_completion_rate, 2)}%"])
        writer.writerow(['Total Distance', f"{round(total_distance, 2)} km", ''])
        writer.writerow([])

        # Write top drivers
        writer.writerow(['Top Drivers by Completed Routes'])
        writer.writerow(['Driver Name', 'Completed Routes', 'Total Distance (km)', 'Avg Duration (min)'])

        top_drivers = db.session.query(
            Driver.id,
            User.first_name,
            User.last_name,
            func.count(Route.id).label('route_count')
        ).join(
            User, Driver.id == User.id
        ).join(
            Route, Driver.id == Route.driver_id
        ).filter(
            Route.company_id == company_id,
            Route.status == RouteStatus.COMPLETED,
            Route.start_time.between(start_date, end_date)
        ).group_by(
            Driver.id, User.first_name, User.last_name
        ).order_by(
            func.count(Route.id).desc()
        ).limit(5).all()

        for driver_id, first_name, last_name, route_count in top_drivers:
            # Calculate total distance
            driver_distance_query = db.session.query(func.sum(Route.distance)).filter(
                Route.driver_id == driver_id,
                Route.status == RouteStatus.COMPLETED,
                Route.start_time.between(start_date, end_date)
            )
            driver_distance = driver_distance_query.scalar() or 0

            # Calculate average duration
            avg_duration_query = db.session.query(
                func.avg(
                    func.extract('epoch', Route.end_time) -
                    func.extract('epoch', Route.start_time)
                ) / 60  # Convert to minutes
            ).filter(
                Route.driver_id == driver_id,
                Route.status == RouteStatus.COMPLETED,
                Route.start_time.isnot(None),
                Route.end_time.isnot(None),
                Route.start_time.between(start_date, end_date)
            )
            avg_duration = avg_duration_query.scalar() or 0

            writer.writerow([
                f"{first_name} {last_name}",
                route_count,
                round(driver_distance, 2),
                round(avg_duration)
            ])

        # Prepare response
        output.seek(0)
        filename = f"company_report_{company.name}_{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}.csv"

        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )

    elif format_type == 'excel':
        # For excel format, we would use openpyxl or xlsxwriter
        # This is a placeholder that returns CSV instead
        flash('Excel export is not implemented yet. Returning CSV format instead.', 'warning')
        return _generate_company_report(company_id, start_date, end_date, 'csv')

    elif format_type == 'pdf':
        # For PDF format, we would use a library like ReportLab
        # This is a placeholder that returns CSV instead
        flash('PDF export is not implemented yet. Returning CSV format instead.', 'warning')
        return _generate_company_report(company_id, start_date, end_date, 'csv')

    else:
        flash('Invalid export format.', 'danger')
        return redirect(url_for('statistics.reports'))