import traceback
from datetime import datetime
from flask import current_app
import os

from flask_login import current_user
from werkzeug.security import generate_password_hash
from sqlalchemy import func, and_, or_
from sqlalchemy.exc import SQLAlchemyError

from app import db
from models import (
    User, UserRole, Admin, CompanyOwner, Manager, Operator, Driver,
    Company, Task, TaskStatus, Route, RouteStatus, Document, Log, ActionType, Message, Statistics
)
from utils import log_action, save_profile_image, save_document, delete_file


class UserService:
    @staticmethod
    def create_user(form, db_session, role=None, save_changes=True):
        """
        Create a new user from form data

        Args:
            form: Form containing user data
            db_session: SQLAlchemy session
            role: Override role from form
            save_changes: Whether to commit changes to database

        Returns:
            Created user object
        """
        try:
            current_app.logger.info(f"Creating new user with username: {form.username.data}, email: {form.email.data}")

            # Create user instance
            user = User(
                username=form.username.data,
                email=form.email.data,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                phone=form.phone.data,
                role=UserRole(role if role else form.role.data),
                is_active=form.is_active.data if hasattr(form, 'is_active') else True,
            )

            current_app.logger.info(f"Setting password for user {form.username.data}")
            try:
                user.set_password(form.password.data)
            except Exception as password_error:
                current_app.logger.error(f"Error setting password: {str(password_error)}")
                raise

            # Handle profile image if present
            if hasattr(form, 'profile_image') and form.profile_image.data:
                current_app.logger.info(f"Processing profile image for user {form.username.data}")
                user.profile_image = save_profile_image(form.profile_image.data)
                if user.profile_image is None:
                    current_app.logger.warning(f"Failed to save profile image for user {form.username.data}")

            current_app.logger.info(f"Adding user {form.username.data} to database session")
            db_session.add(user)

            # Process role-specific data
            if role or (hasattr(form, 'role') and form.role.data):
                user_role = role if role else form.role.data
                current_app.logger.info(f"Processing role-specific data for role: {user_role}")

                if user_role == UserRole.ADMIN.value:
                    current_app.logger.info(f"Creating admin record for user {form.username.data}")
                    try:
                        admin_level = getattr(form, 'admin_level', 1)
                        admin = Admin(id=user.id, admin_level=admin_level)
                        db_session.add(admin)
                    except Exception as admin_error:
                        current_app.logger.error(f"Error creating admin record: {str(admin_error)}")
                        raise

                # Process other roles similarly...

            if save_changes:
                current_app.logger.info(f"Committing changes for user {form.username.data}")
                db_session.commit()
                log_action(ActionType.CREATE, f"Created user {user.username}", db_session)
                current_app.logger.info(f"User {user.username} created successfully")

            return user
        except Exception as e:
            current_app.logger.error(f"Error creating user: {str(e)}")
            current_app.logger.error(f"Traceback: {traceback.format_exc()}")
            db_session.rollback()
            raise

    @staticmethod
    def change_password(user, new_password, db_session):
        """
        Change user password

        Args:
            user: User object
            new_password: New password (plain text)
            db_session: SQLAlchemy session
        """
        try:
            user.password_hash = generate_password_hash(new_password)
            db_session.commit()
            log_action(ActionType.UPDATE, "Changed password", db_session)
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error changing password: {str(e)}")
            raise

    @staticmethod
    def create_admin(form, db_session):
        """
        Create a new admin user

        Args:
            form: Form containing user and admin data
            db_session: SQLAlchemy session

        Returns:
            Created user object
        """
        try:
            user = UserService.create_user(form, db_session, role=UserRole.ADMIN.value, save_changes=False)

            admin = Admin(id=user.id, admin_level=form.admin_level.data)
            db_session.add(admin)

            db_session.commit()
            log_action(ActionType.CREATE, f"Created admin {user.username}", db_session)

            return user
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error creating admin: {str(e)}")
            raise

    @staticmethod
    def create_company_owner(form, db_session):
        """
        Create a new company owner user

        Args:
            form: Form containing user and company owner data
            db_session: SQLAlchemy session

        Returns:
            Created user object
        """
        try:
            user = UserService.create_user(form, db_session, role=UserRole.COMPANY_OWNER.value, save_changes=False)

            company_id = None

            # Handle company creation or selection
            if form.create_new_company.data:
                # Create new company
                company = Company(
                    name=form.company_name.data,
                    legal_name=form.company_legal_name.data,
                    tax_id=form.company_tax_id.data,
                    address=form.company_address.data,
                    phone=form.company_phone.data,
                    email=form.company_email.data,
                    website=form.company_website.data
                )
                db_session.add(company)
                db_session.flush()  # Get company ID
                company_id = company.id
            else:
                # Use existing company
                company_id = form.company_id.data

            # Create company owner relationship
            company_owner = CompanyOwner(id=user.id, company_id=company_id)
            db_session.add(company_owner)

            db_session.commit()
            log_action(ActionType.CREATE, f"Created company owner {user.username}", db_session)

            return user
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error creating company owner: {str(e)}")
            raise

    @staticmethod
    def create_manager(form, company_id, db_session):
        """
        Create a new manager user

        Args:
            form: Form containing user data
            company_id: ID of the company the manager belongs to
            db_session: SQLAlchemy session

        Returns:
            Created user object
        """
        try:
            user = UserService.create_user(form, db_session, role=UserRole.MANAGER.value, save_changes=False)

            # Create manager relationship
            manager = Manager(id=user.id, company_id=company_id)
            db_session.add(manager)

            db_session.commit()
            log_action(ActionType.CREATE, f"Created manager {user.username}", db_session)

            return user
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error creating manager: {str(e)}")
            raise

    @staticmethod
    def create_operator(form, company_id, manager_id, db_session):
        """
        Create a new operator user

        Args:
            form: Form containing user data
            company_id: ID of the company the operator belongs to
            manager_id: ID of the manager the operator reports to
            db_session: SQLAlchemy session

        Returns:
            Created user object
        """
        try:
            user = UserService.create_user(form, db_session, role=UserRole.OPERATOR.value, save_changes=False)

            # Create operator relationship
            operator = Operator(id=user.id, company_id=company_id, manager_id=manager_id)
            db_session.add(operator)

            db_session.commit()
            log_action(ActionType.CREATE, f"Created operator {user.username}", db_session)

            return user
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error creating operator: {str(e)}")
            raise

    @staticmethod
    def create_driver(form, company_id, operator_id, db_session):
        """
        Create a new driver user

        Args:
            form: Form containing user and driver data
            company_id: ID of the company the driver belongs to
            operator_id: ID of the operator the driver reports to
            db_session: SQLAlchemy session

        Returns:
            Created user object
        """
        try:
            user = UserService.create_user(form, db_session, role=UserRole.DRIVER.value, save_changes=False)

            # Create driver relationship
            driver = Driver(
                id=user.id,
                company_id=company_id,
                operator_id=operator_id,
                license_number=form.license_number.data,
                vehicle_info=form.vehicle_info.data
            )
            db_session.add(driver)

            db_session.commit()
            log_action(ActionType.CREATE, f"Created driver {user.username}", db_session)

            return user
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error creating driver: {str(e)}")
            raise

    @staticmethod
    def search_users(query, role=None, company_id=None, page=1, per_page=10, db_session=None):
        """
        Search for users with filtering options

        Args:
            query: Search query string
            role: Filter by user role (optional)
            company_id: Filter by company ID (optional)
            page: Page number
            per_page: Items per page
            db_session: SQLAlchemy session (optional)

        Returns:
            Pagination object with user results
        """
        search_query = User.query

        # Apply search filters
        if query:
            search_term = f"%{query}%"
            search_query = search_query.filter(
                or_(
                    User.username.ilike(search_term),
                    User.email.ilike(search_term),
                    User.first_name.ilike(search_term),
                    User.last_name.ilike(search_term),
                    User.phone.ilike(search_term)
                )
            )

        # Filter by role
        if role:
            search_query = search_query.filter(User.role == role)

        # Filter by company
        if company_id:
            search_query = search_query.join(
                CompanyOwner,
                and_(
                    User.id == CompanyOwner.id,
                    User.role == UserRole.COMPANY_OWNER,
                    CompanyOwner.company_id == company_id
                ),
                isouter=True
            ).join(
                Manager,
                and_(
                    User.id == Manager.id,
                    User.role == UserRole.MANAGER,
                    Manager.company_id == company_id
                ),
                isouter=True
            ).join(
                Operator,
                and_(
                    User.id == Operator.id,
                    User.role == UserRole.OPERATOR,
                    Operator.company_id == company_id
                ),
                isouter=True
            ).join(
                Driver,
                and_(
                    User.id == Driver.id,
                    User.role == UserRole.DRIVER,
                    Driver.company_id == company_id
                ),
                isouter=True
            ).filter(
                or_(
                    CompanyOwner.company_id == company_id,
                    Manager.company_id == company_id,
                    Operator.company_id == company_id,
                    Driver.company_id == company_id
                )
            )

        # Order by username
        search_query = search_query.order_by(User.username)

        # Paginate results
        return search_query.paginate(page=page, per_page=per_page)

    @staticmethod
    def update_user(user, form, db_session, save_changes=True):
        """
        Update an existing user from form data

        Args:
            user: User object to update
            form: Form containing updated user data
            db_session: SQLAlchemy session
            save_changes: Whether to commit changes to database

        Returns:
            Updated user object
        """
        try:
            current_app.logger.info(f"Updating user {user.username}")

            # Update basic user information
            user.username = form.username.data
            user.email = form.email.data
            user.first_name = form.first_name.data
            user.last_name = form.last_name.data
            user.phone = form.phone.data

            # Update role if present in form
            if hasattr(form, 'role') and form.role.data:
                # Only admins can change roles
                if current_user.role == UserRole.ADMIN:
                    user.role = UserRole(form.role.data)

            # Update active status if present in form
            if hasattr(form, 'is_active'):
                user.is_active = form.is_active.data

            # Handle profile image if present
            if hasattr(form, 'profile_image') and form.profile_image.data:
                current_app.logger.info(f"Processing new profile image for user {user.username}")
                new_profile_image = save_profile_image(form.profile_image.data)
                if new_profile_image:
                    # If user already has a profile image, delete the old one
                    if user.profile_image:
                        try:
                            # Delete old profile image
                            delete_file(user.profile_image)
                        except Exception as e:
                            current_app.logger.warning(f"Error deleting old profile image: {str(e)}")

                    user.profile_image = new_profile_image
                else:
                    current_app.logger.warning(f"Failed to save new profile image for user {user.username}")

            # Process role-specific data if necessary
            if user.role == UserRole.ADMIN and hasattr(form, 'admin_level'):
                if not user.admin:
                    # Create admin record if it doesn't exist
                    admin = Admin(id=user.id, admin_level=form.admin_level.data)
                    db_session.add(admin)
                else:
                    # Update existing admin record
                    user.admin.admin_level = form.admin_level.data

            # Similar updates for other role-specific data can be added here
            # For example, updating driver license information, etc.

            if save_changes:
                current_app.logger.info(f"Committing changes for user {user.username}")
                db_session.commit()
                log_action(ActionType.UPDATE, f"Updated user {user.username}", db_session)
                current_app.logger.info(f"User {user.username} updated successfully")

            return user
        except Exception as e:
            current_app.logger.error(f"Error updating user: {str(e)}")
            current_app.logger.error(f"Traceback: {traceback.format_exc()}")
            db_session.rollback()
            raise


class CompanyService:
    @staticmethod
    def create_company(form, db_session):
        """
        Create a new company from form data

        Args:
            form: Form containing company data
            db_session: SQLAlchemy session

        Returns:
            Created company object
        """
        try:
            company = Company(
                name=form.name.data,
                legal_name=form.legal_name.data,
                tax_id=form.tax_id.data,
                address=form.address.data,
                phone=form.phone.data,
                email=form.email.data,
                website=form.website.data
            )

            db_session.add(company)
            db_session.commit()
            log_action(ActionType.CREATE, f"Created company {company.name}", db_session)

            return company
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error creating company: {str(e)}")
            raise

    @staticmethod
    def update_company(company, form, db_session):
        """
        Update existing company from form data

        Args:
            company: Company object to update
            form: Form containing updated company data
            db_session: SQLAlchemy session

        Returns:
            Updated company object
        """
        try:
            company.name = form.name.data
            company.legal_name = form.legal_name.data
            company.tax_id = form.tax_id.data
            company.address = form.address.data
            company.phone = form.phone.data
            company.email = form.email.data
            company.website = form.website.data

            db_session.commit()
            log_action(ActionType.UPDATE, f"Updated company {company.name}", db_session)

            return company
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error updating company: {str(e)}")
            raise

    @staticmethod
    def get_company_metrics(company, db_session):
        """
        Get metrics for a company

        Args:
            company: Company object
            db_session: SQLAlchemy session

        Returns:
            Dictionary with company metrics
        """
        try:
            # Get company-related statistics
            manager_count = db_session.query(Manager).filter_by(company_id=company.id).count()
            operator_count = db_session.query(Operator).filter_by(company_id=company.id).count()
            driver_count = db_session.query(Driver).filter_by(company_id=company.id).count()

            # Get task and route statistics
            task_count = db_session.query(Task).filter_by(company_id=company.id).count()
            completed_tasks = db_session.query(Task).filter_by(
                company_id=company.id,
                status=TaskStatus.COMPLETED
            ).count()
            in_progress_tasks = db_session.query(Task).filter_by(
                company_id=company.id,
                status=TaskStatus.IN_PROGRESS
            ).count()

            active_routes = db_session.query(Route).filter_by(
                company_id=company.id,
                status=RouteStatus.IN_PROGRESS
            ).count()

            # Calculate completion rate
            completion_rate = (completed_tasks / task_count * 100) if task_count > 0 else 0

            return {
                'managers': manager_count,
                'operators': operator_count,
                'drivers': driver_count,
                'tasks': task_count,
                'completed_tasks': completed_tasks,
                'in_progress_tasks': in_progress_tasks,
                'active_routes': active_routes,
                'completion_rate': completion_rate
            }
        except Exception as e:
            current_app.logger.error(f"Error getting company metrics: {str(e)}")
            # Return empty metrics on error
            return {
                'managers': 0,
                'operators': 0,
                'drivers': 0,
                'tasks': 0,
                'completed_tasks': 0,
                'in_progress_tasks': 0,
                'active_routes': 0,
                'completion_rate': 0
            }

    @staticmethod
    def search_companies(query, page=1, per_page=10):
        """
        Search for companies

        Args:
            query: Search query string
            page: Page number
            per_page: Items per page

        Returns:
            Pagination object with company results
        """
        search_query = Company.query

        # Apply search filters
        if query:
            search_term = f"%{query}%"
            search_query = search_query.filter(
                or_(
                    Company.name.ilike(search_term),
                    Company.legal_name.ilike(search_term),
                    Company.tax_id.ilike(search_term),
                    Company.email.ilike(search_term),
                    Company.phone.ilike(search_term)
                )
            )

        # Order by name
        search_query = search_query.order_by(Company.name)

        # Paginate results
        return search_query.paginate(page=page, per_page=per_page)


class TaskService:
    @staticmethod
    def create_task(form, creator_id, company_id, db_session):
        """
        Create a new task

        Args:
            form: Form containing task data
            creator_id: ID of the user creating the task
            company_id: ID of the company the task belongs to
            db_session: SQLAlchemy session

        Returns:
            Created task object
        """
        try:
            task = Task(
                title=form.title.data,
                description=form.description.data,
                status=TaskStatus.NEW,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                deadline=form.deadline.data,
                creator_id=creator_id,
                assignee_id=form.assignee_id.data if hasattr(form, 'assignee_id') and form.assignee_id.data != 0 else None,
                company_id=company_id
            )

            db_session.add(task)
            db_session.commit()
            log_action(ActionType.CREATE, f"Created task {task.title}", db_session)

            return task
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error creating task: {str(e)}")
            raise

    @staticmethod
    def update_task(task, form, db_session):
        """
        Update an existing task

        Args:
            task: Task object to update
            form: Form containing updated task data
            db_session: SQLAlchemy session

        Returns:
            Updated task object
        """
        try:
            task.title = form.title.data
            task.description = form.description.data
            task.status = form.status.data
            task.deadline = form.deadline.data

            if hasattr(form, 'assignee_id'):
                task.assignee_id = form.assignee_id.data

            db_session.commit()
            log_action(ActionType.UPDATE, f"Updated task {task.title}", db_session)

            return task
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error updating task: {str(e)}")
            raise

    @staticmethod
    def add_document(task, file, uploader_id, db_session):
        """
        Add a document to a task

        Args:
            task: Task object
            file: File object from form
            uploader_id: ID of the user uploading the document
            db_session: SQLAlchemy session

        Returns:
            Created document object or None if file is invalid
        """
        try:
            file_path, file_type, file_size = save_document(file, task.id, task.company_id)

            if not file_path:
                return None

            document = Document(
                title=file.filename,
                file_path=file_path,
                file_type=file_type,
                size=file_size,
                uploader_id=uploader_id,
                task_id=task.id,
                company_id=task.company_id
            )

            db_session.add(document)
            db_session.commit()
            log_action(ActionType.UPLOAD, f"Added document {document.title} to task {task.title}", db_session)

            return document
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error adding document: {str(e)}")
            return None

    @staticmethod
    def search_tasks(query, company_id=None, status=None, creator_id=None, assignee_id=None,
                     page=1, per_page=10):
        """
        Search for tasks with filtering options

        Args:
            query: Search query string
            company_id: Filter by company ID (optional)
            status: Filter by task status (optional)
            creator_id: Filter by creator user ID (optional)
            assignee_id: Filter by assignee user ID (optional)
            page: Page number
            per_page: Items per page

        Returns:
            Pagination object with task results
        """
        search_query = Task.query

        # Apply search filters
        if query:
            search_term = f"%{query}%"
            search_query = search_query.filter(
                or_(
                    Task.title.ilike(search_term),
                    Task.description.ilike(search_term)
                )
            )

        # Apply additional filters
        if company_id:
            search_query = search_query.filter(Task.company_id == company_id)

        if status:
            search_query = search_query.filter(Task.status == status)

        if creator_id:
            search_query = search_query.filter(Task.creator_id == creator_id)

        if assignee_id:
            search_query = search_query.filter(Task.assignee_id == assignee_id)

        # Order by creation date (newest first)
        search_query = search_query.order_by(Task.created_at.desc())

        # Paginate results
        return search_query.paginate(page=page, per_page=per_page)




class RouteService:
    @staticmethod
    def create_route(form, task_id, driver_id, company_id, db_session):
        """
        Create a new route

        Args:
            form: Form containing route data
            task_id: ID of the task the route belongs to
            driver_id: ID of the driver assigned to the route
            company_id: ID of the company the route belongs to
            db_session: SQLAlchemy session

        Returns:
            Created route object
        """
        try:
            route = Route(
                start_point=form.start_point.data,
                end_point=form.end_point.data,
                waypoints=form.waypoints.data if hasattr(form, 'waypoints') else None,
                distance=form.distance.data if hasattr(form, 'distance') else None,
                estimated_time=form.estimated_time.data if hasattr(form, 'estimated_time') else None,
                start_time=form.start_time.data if hasattr(form, 'start_time') else None,
                status=RouteStatus.PLANNED,
                driver_id=driver_id,
                task_id=task_id,
                company_id=company_id
            )

            db_session.add(route)
            db_session.commit()
            log_action(ActionType.CREATE, f"Created route from {route.start_point} to {route.end_point}", db_session)

            return route
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error creating route: {str(e)}")
            raise

    @staticmethod
    def update_route(route, form, db_session):
        """
        Update an existing route

        Args:
            route: Route object to update
            form: Form containing updated route data
            db_session: SQLAlchemy session

        Returns:
            Updated route object
        """
        try:
            route.start_point = form.start_point.data
            route.end_point = form.end_point.data

            if hasattr(form, 'waypoints'):
                route.waypoints = form.waypoints.data

            if hasattr(form, 'distance'):
                route.distance = form.distance.data

            if hasattr(form, 'estimated_time'):
                route.estimated_time = form.estimated_time.data

            if hasattr(form, 'start_time'):
                route.start_time = form.start_time.data

            if hasattr(form, 'end_time'):
                route.end_time = form.end_time.data

            if hasattr(form, 'status'):
                route.status = form.status.data

                # If route is completed, update task status
                if route.status == RouteStatus.COMPLETED and route.task.status != TaskStatus.COMPLETED:
                    route.task.status = TaskStatus.COMPLETED

                # If route is cancelled, update task status
                if route.status == RouteStatus.CANCELLED and route.task.status != TaskStatus.CANCELLED:
                    route.task.status = TaskStatus.CANCELLED

            db_session.commit()
            log_action(ActionType.UPDATE, f"Updated route from {route.start_point} to {route.end_point}", db_session)

            return route
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error updating route: {str(e)}")
            raise

    @staticmethod
    def get_active_routes_for_driver(driver_id, page=1, per_page=10):
        """
        Get active routes for a driver

        Args:
            driver_id: ID of the driver
            page: Page number
            per_page: Items per page

        Returns:
            Pagination object with route results
        """
        route_query = Route.query.filter(
            Route.driver_id == driver_id,
            Route.status.in_([RouteStatus.PLANNED, RouteStatus.IN_PROGRESS])
        ).order_by(Route.start_time)

        return route_query.paginate(page=page, per_page=per_page)


class MessageService:
    @staticmethod
    def send_message(sender_id, recipient_id, task_id, content, company_id, db_session):
        """
        Send a message

        Args:
            sender_id: ID of the sender
            recipient_id: ID of the recipient
            task_id: ID of the task the message is related to (optional)
            content: Message content
            company_id: ID of the company the message belongs to
            db_session: SQLAlchemy session

        Returns:
            Created message object
        """
        try:
            message = Message(
                content=content,
                is_read=False,
                sender_id=sender_id,
                recipient_id=recipient_id,
                task_id=task_id,
                company_id=company_id
            )

            db_session.add(message)
            db_session.commit()
            log_action(ActionType.CREATE, f"Sent message to user ID {recipient_id}", db_session)

            return message
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error sending message: {str(e)}")
            raise

    @staticmethod
    def mark_as_read(message, db_session):
        """
        Mark a message as read

        Args:
            message: Message object
            db_session: SQLAlchemy session

        Returns:
            Updated message object
        """
        try:
            message.is_read = True
            db_session.commit()
            return message
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error marking message as read: {str(e)}")
            raise

    @staticmethod
    def get_messages_between_users(user1_id, user2_id, page=1, per_page=20):
        """
        Get messages between two users

        Args:
            user1_id: ID of first user
            user2_id: ID of second user
            page: Page number
            per_page: Items per page

        Returns:
            Pagination object with message results
        """
        messages_query = Message.query.filter(
            or_(
                and_(
                    Message.sender_id == user1_id,
                    Message.recipient_id == user2_id
                ),
                and_(
                    Message.sender_id == user2_id,
                    Message.recipient_id == user1_id
                )
            )
        ).order_by(Message.sent_at.desc())

        return messages_query.paginate(page=page, per_page=per_page)

    @staticmethod
    def get_unread_messages_count(user_id):
        """
        Get count of unread messages for a user

        Args:
            user_id: ID of the user

        Returns:
            Count of unread messages
        """
        return Message.query.filter(
            Message.recipient_id == user_id,
            Message.is_read == False
        ).count()


class AuthService:
    @staticmethod
    def login_user(user, db_session):
        """
        Log user login

        Args:
            user: User object
            db_session: SQLAlchemy session
        """
        try:
            user.last_login = datetime.utcnow()
            db_session.commit()  # Исправлено с db_session.session.commit()
            log_action(ActionType.LOGIN, f"User {user.username} logged in", db_session)
        except Exception as e:
            db_session.rollback()  # Исправлено с db_session.session.rollback()
            current_app.logger.error(f"Error logging user login: {str(e)}")

    @staticmethod
    def logout_user(user, db_session):
        """
        Log user logout

        Args:
            user: User object
            db_session: SQLAlchemy session
        """
        try:
            log_action(ActionType.LOGOUT, f"User {user.username} logged out", db_session)
        except Exception as e:
            current_app.logger.error(f"Error logging user logout: {str(e)}")


class LogService:
    @staticmethod
    def get_recent_logs(limit=100, user_id=None, action_type=None, company_id=None):
        """
        Get recent log entries with filtering options

        Args:
            limit: Maximum number of logs to return
            user_id: Filter by user ID (optional)
            action_type: Filter by action type (optional)
            company_id: Filter by company ID (optional)

        Returns:
            List of Log objects
        """
        logs_query = Log.query

        # Apply filters
        if user_id:
            logs_query = logs_query.filter(Log.user_id == user_id)

        if action_type:
            logs_query = logs_query.filter(Log.action_type == action_type)

        if company_id:
            logs_query = logs_query.filter(Log.company_id == company_id)

        # Order by timestamp (most recent first)
        logs_query = logs_query.order_by(Log.timestamp.desc())

        # Limit results
        return logs_query.limit(limit).all()

    @staticmethod
    def get_paginated_logs(page=1, per_page=20, user_id=None, action_type=None, company_id=None):
        """
        Get paginated log entries with filtering options

        Args:
            page: Page number
            per_page: Items per page
            user_id: Filter by user ID (optional)
            action_type: Filter by action type (optional)
            company_id: Filter by company ID (optional)

        Returns:
            Pagination object with log results
        """
        logs_query = Log.query

        # Apply filters
        if user_id:
            logs_query = logs_query.filter(Log.user_id == user_id)

        if action_type:
            logs_query = logs_query.filter(Log.action_type == action_type)

        if company_id:
            logs_query = logs_query.filter(Log.company_id == company_id)

        # Order by timestamp (most recent first)
        logs_query = logs_query.order_by(Log.timestamp.desc())

        # Paginate results
        return logs_query.paginate(page=page, per_page=per_page)

    @staticmethod
    def get_user_logs(user_id, limit=100):
        """
        Get recent log entries for a specific user

        Args:
            user_id: User ID
            limit: Maximum number of logs to return

        Returns:
            List of Log objects
        """
        return Log.query.filter_by(user_id=user_id).order_by(Log.timestamp.desc()).limit(limit).all()

    @staticmethod
    def get_action_counts_by_user(company_id=None, days=30):
        """
        Get count of actions grouped by user within a time period

        Args:
            company_id: Filter by company ID (optional)
            days: Number of days to include

        Returns:
            List of dictionaries with user_id, username, and action_count
        """
        from sqlalchemy import func
        from datetime import datetime, timedelta

        # Calculate the start date based on days parameter
        start_date = datetime.utcnow() - timedelta(days=days)

        # Base query
        query = db.session.query(
            Log.user_id,
            User.username,
            func.count(Log.id).label('action_count')
        ).join(User, Log.user_id == User.id)

        # Apply filters
        query = query.filter(Log.timestamp >= start_date)

        if company_id:
            query = query.filter(Log.company_id == company_id)

        # Group by user and order by count (descending)
        query = query.group_by(Log.user_id, User.username).order_by(func.count(Log.id).desc())

        # Execute query and return results
        results = query.all()

        # Convert results to dictionaries
        return [
            {
                'user_id': result[0],
                'username': result[1],
                'action_count': result[2]
            }
            for result in results
        ]


class StatisticsService:
    @staticmethod
    def update_company_statistics(company_id, db_session):
        """
        Update statistics for a company

        Args:
            company_id: ID of the company
            db_session: SQLAlchemy session

        Returns:
            Updated statistics object
        """
        try:
            from datetime import datetime, timedelta

            # Get the company
            company = Company.query.get(company_id)
            if not company:
                raise ValueError(f"Company with ID {company_id} not found")

            # Define period (last 30 days)
            period_end = datetime.utcnow()
            period_start = period_end - timedelta(days=30)

            # Get existing statistics or create new one
            stats = db_session.query(Statistics).filter(
                Statistics.company_id == company_id,
                Statistics.user_id == None,  # Company-level statistics
                Statistics.period_start == period_start.replace(hour=0, minute=0, second=0, microsecond=0)
            ).first()

            if not stats:
                stats = Statistics(
                    company_id=company_id,
                    user_id=None,
                    period_start=period_start.replace(hour=0, minute=0, second=0, microsecond=0),
                    period_end=period_end.replace(hour=23, minute=59, second=59, microsecond=999),
                    metrics={}
                )
                db_session.add(stats)

            # Get metrics
            metrics = CompanyService.get_company_metrics(company, db_session)

            # Update statistics
            stats.metrics = metrics
            stats.calculated_at = datetime.utcnow()

            db_session.commit()
            return stats
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error updating company statistics: {str(e)}")
            raise

    @staticmethod
    def update_user_statistics(user_id, db_session):
        """
        Update statistics for a user

        Args:
            user_id: ID of the user
            db_session: SQLAlchemy session

        Returns:
            Updated statistics object
        """
        try:
            from datetime import datetime, timedelta
            from sqlalchemy import func

            # Get the user
            user = User.query.get(user_id)
            if not user:
                raise ValueError(f"User with ID {user_id} not found")

            # Get company ID based on user role
            company_id = None
            if user.role == UserRole.COMPANY_OWNER and user.company_owner:
                company_id = user.company_owner.company_id
            elif user.role == UserRole.MANAGER and user.manager:
                company_id = user.manager.company_id
            elif user.role == UserRole.OPERATOR and user.operator:
                company_id = user.operator.company_id
            elif user.role == UserRole.DRIVER and user.driver:
                company_id = user.driver.company_id

            if not company_id:
                raise ValueError(f"User with ID {user_id} is not associated with a company")

            # Define period (last 30 days)
            period_end = datetime.utcnow()
            period_start = period_end - timedelta(days=30)

            # Get existing statistics or create new one
            stats = db_session.query(Statistics).filter(
                Statistics.company_id == company_id,
                Statistics.user_id == user_id,
                Statistics.period_start == period_start.replace(hour=0, minute=0, second=0, microsecond=0)
            ).first()

            if not stats:
                stats = Statistics(
                    company_id=company_id,
                    user_id=user_id,
                    period_start=period_start.replace(hour=0, minute=0, second=0, microsecond=0),
                    period_end=period_end.replace(hour=23, minute=59, second=59, microsecond=999),
                    metrics={}
                )
                db_session.add(stats)

            # Calculate user-specific metrics based on role
            metrics = {}

            # Actions count
            action_count = db_session.query(func.count(Log.id)).filter(
                Log.user_id == user_id,
                Log.timestamp.between(period_start, period_end)
            ).scalar()
            metrics['action_count'] = action_count

            if user.role == UserRole.MANAGER:
                # Count operators under this manager
                operator_count = db_session.query(func.count(Operator.id)).filter(
                    Operator.manager_id == user.id
                ).scalar()
                metrics['operator_count'] = operator_count

                # Count tasks created by this manager
                task_count = db_session.query(func.count(Task.id)).filter(
                    Task.creator_id == user_id,
                    Task.created_at.between(period_start, period_end)
                ).scalar()
                metrics['task_count'] = task_count

            elif user.role == UserRole.OPERATOR:
                # Count drivers under this operator
                driver_count = db_session.query(func.count(Driver.id)).filter(
                    Driver.operator_id == user.id
                ).scalar()
                metrics['driver_count'] = driver_count

                # Count tasks created by this operator
                task_count = db_session.query(func.count(Task.id)).filter(
                    Task.creator_id == user_id,
                    Task.created_at.between(period_start, period_end)
                ).scalar()
                metrics['task_count'] = task_count

            elif user.role == UserRole.DRIVER:
                # Count routes assigned to this driver
                route_count = db_session.query(func.count(Route.id)).filter(
                    Route.driver_id == user.id,
                    Route.created_at.between(period_start, period_end)
                ).scalar()
                metrics['route_count'] = route_count

                # Count completed routes
                completed_routes = db_session.query(func.count(Route.id)).filter(
                    Route.driver_id == user.id,
                    Route.status == RouteStatus.COMPLETED,
                    Route.created_at.between(period_start, period_end)
                ).scalar()
                metrics['completed_routes'] = completed_routes

                # Calculate completion rate
                metrics['completion_rate'] = (completed_routes / route_count * 100) if route_count > 0 else 0

            # Update statistics
            stats.metrics = metrics
            stats.calculated_at = datetime.utcnow()

            db_session.commit()
            return stats
        except Exception as e:
            db_session.rollback()
            current_app.logger.error(f"Error updating user statistics: {str(e)}")
            raise