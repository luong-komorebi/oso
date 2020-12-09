from typing import Any, List

from sqlalchemy.types import Integer, String
from sqlalchemy.schema import Table, Column, ForeignKey
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship, backref
from sqlalchemy import inspect


ROLE_CLASSES: List[Any] = []


def enable_roles(oso):
    """Enable the SQLAlchemy Role-Based Access Control base policy. This method activates the following polar rules:

        `role_allow(role, action, resource)`:
            Allows actors that have the role ``role`` to take ``action`` on
            ``resource``. ``role`` is a SQLAlchemy role model generated by
            :py:meth:`sqlalchemy_oso.roles.resource_role_class`. ``resource``
            is a SQLAlchemy model to which the ``role`` applies. Roles apply
            to the resources they are scoped to, For example,
            ``OrganizationRole`` roles apply to ``Organization`` resources.
            Roles may also apply to resources as specified by
            ``resource_role_applies_to`` Polar rules.

            E.g.,
            ```
            role_allow(role: OrganizationRole{name: "MEMBER"}, "READ", org: Organization);
            ```



        ``resource_role_applies_to(child_resource, parent_resource)``:
            Permits roles that control access to `parent_resource` apply to
            `child_resource` as well. `parent_resource` must be a resource
            that has a resource role class associated with it (see
            :py:meth:`sqlalchemy_oso.roles.resource_role_class`).

            E.g.,
                ```
                ### An organization's roles apply to its child repositories
                resource_role_applies_to(repo: Repository, parent_org) if
                    parent_org = repo.organization;
                ```

            The above rule makes it possible to write `role_allow` rules
            between `OrganizationRole` and `Repository`.
            E.g.,
                ```
                role_allow(role: OrganizationRole{name: "MEMBER"}, "READ", repo: Repository);
                ```
        ``[resource_name]_role_order(["ROLE_NAME_1", "ROLE_NAME_2",...])``:
            Specifies a hierarchical role order for built-in
            resource-specific roles defined with
            :py:meth:`sqlalchemy_oso.roles.resource_role_class` The rule name
            is the lowercased resource model name followed by
            ``_role_order``. The only parameter is a list of role names in
            hierarchical order. Roles to the left will inherit the
            permissions of roles to the right. This is useful if any role
            should inherit all the permissions of another role. It is not
            required for all built-in roles to be specified in the list.

            E.g.,
                ```
                repository_role_order(["ADMIN", "MAINTAIN", "WRITE", "TRIAGE", "READ"]);
                ```
                Is the equivalent of writing:
                ```
                role_allow(role: RepositoryRole{name: "ADMIN"}, _action, _resource) if
                    role_allow(new RepositoryRole{name: "MAINTAIN"}, _action, _resource);

                role_allow(role: RepositoryRole{name: "MAINTAIN"}, _action, _resource) if
                    role_allow(new RepositoryRole{name: "WRITE"}, _action, _resource);
                ```
                ...and so on.




    :param [oso]: [The Oso instance used to evaluate the policy.]
    :type [oso]: [Oso]
    """

    global ROLE_CLASSES

    policy = """
    # RBAC BASE POLICY

    ## Top-level RBAC allow rule

    allow(user, action, resource) if
        rbac_allow(user, action, resource);

    ### The association between the resource roles and the requested resource is outsourced from the rbac_allow
    rbac_allow(user, action, resource) if
        resource_role_applies_to(resource, role_resource) and
        user_in_role(user, role, role_resource) and
        role_allow(role, action, resource);

    # RESOURCE-ROLE RELATIONSHIPS

    ## These rules allow roles to apply to resources other than those that they are scoped to.
    ## The most common example of this is nested resources, e.g. Repository roles should apply to the Issues
    ## nested in that repository.

    ### A resource's roles applies to itself
    resource_role_applies_to(role_resource, role_resource);

    # ROLE-ROLE RELATIONSHIPS

    ## Role Hierarchies

    ### Grant a role permissions that it inherits from a more junior role
    role_allow(role, action, resource) if
        inherits_role(role, inherited_role) and
        role_allow(inherited_role, action, resource);

    ### Helper to determine relative order or roles in a list
    inherits_role_helper(role, inherited_role, role_order) if
        ([first, *rest] = role_order and
        role = first and
        inherited_role in rest) or
        ([first, *rest] = role_order and
        inherits_role_helper(role, inherited_role, rest));
    """

    for role_model in ROLE_CLASSES:
        User = role_model["user_model"].__name__
        Resource = role_model["resource_model"].__name__
        Group = role_model["group_model"]
        if Group:
            Group = Group.__name__
        Role = role_model["role_model"]

        policy += f"""
        user_in_role(user: {User}, role, resource: {Resource}) if
            session = OsoSession.get() and
            role in session.query({Role}).filter({Role}.users.any({User}.id.__eq__(user.id))) and
            role.{Resource.lower()}.id = resource.id;

        inherits_role(role: {Role}, inherited_role) if
            {Resource.lower()}_role_order(role_order) and
            inherits_role_helper(role.name, inherited_role_name, role_order) and
            inherited_role = new {Role}(name: inherited_role_name, {Resource.lower()}: role.{Resource.lower()});
        """

    # @TODO: Group
    oso.load_str(policy)


def resource_role_class(
    declarative_base, user_model, resource_model, roles, group_model=None
):
    """Create a resource-specific Role Mixin for SQLAlchemy models.


    :param [declarative_base]: [The SQLAlchemy declarative base model that
    the role model and all related models are mapped to.]
    :type [declarative_base]: [Oso]
    """
    global ROLE_CLASSES
    ROLE_CLASSES.append(
        {
            "user_model": user_model,
            "resource_model": resource_model,
            "group_model": group_model,
            # @NOTE: Must name role model like this for now.
            "role_model": resource_model.__name__ + "Role",
        }
    )

    # many-to-many relationship with users
    user_join_table = Table(
        f"{resource_model.__name__.lower()}_roles_users",
        declarative_base.metadata,
        Column(
            f"{resource_model.__name__.lower()}_role_id",
            Integer,
            ForeignKey(f"{resource_model.__name__.lower()}_roles.id"),
            primary_key=True,
        ),
        Column(
            "user_id",
            Integer,
            ForeignKey(f"{user_model.__tablename__}.id"),
            primary_key=True,
        ),
    )

    class ResourceRoleMixin:
        # TODO: enforce that classes are named with the ResourceRole convention, e.g. RepositorRole
        choices = roles

        __tablename__ = f"{resource_model.__name__.lower()}_roles"
        id = Column(Integer, primary_key=True)
        name = Column(String())

        # many-to-many relationship with users
        @declared_attr
        def users(cls):
            return relationship(
                f"{user_model.__name__}",
                secondary=user_join_table,
                lazy="subquery",
                backref=backref(f"{resource_model.__name__.lower()}_roles", lazy=True),
            )

    @declared_attr
    def resource_id(cls):
        table_name = resource_model.__tablename__
        return Column(Integer, ForeignKey(f"{table_name}.id"))

    @declared_attr
    def resource(cls):
        return relationship(resource_model.__name__, backref="roles", lazy=True)

    setattr(ResourceRoleMixin, f"{resource_model.__name__.lower()}_id", resource_id)
    setattr(ResourceRoleMixin, resource_model.__name__.lower(), resource)

    if group_model:
        group_join_table = Table(
            f"{resource_model.__name__.lower()}_roles_groups",
            declarative_base.metadata,
            Column(
                f"{resource_model.__name__.lower()}_role_id",
                Integer,
                ForeignKey(f"{resource_model.__name__.lower()}_roles.id"),
                primary_key=True,
            ),
            Column(
                "group_id",
                Integer,
                ForeignKey(f"{group_model.__tablename__}.id"),
                primary_key=True,
            ),
        )

        @declared_attr
        def groups(cls):
            return relationship(
                f"{group_model.__name__}",
                secondary=group_join_table,
                lazy="subquery",
                backref=backref(f"{group_model.__name__.lower()}_roles", lazy=True),
            )

        setattr(ResourceRoleMixin, "groups", groups)

    return ResourceRoleMixin


def get_role_model_for_resource_model(resource_model):
    return inspect(resource_model).relationships.get("roles").argument.class_


def get_user_model_for_resource_model(resource_model):
    role_model = get_role_model_for_resource_model(resource_model)
    return inspect(role_model).relationships.get("users").argument()


# Generic way to get a user's resources and roles for any resource model
def get_user_resources_and_roles(session, user, resource_model):
    """Get a user's roles for a all resources of a single resource type"""
    role_model = get_role_model_for_resource_model(resource_model)
    user_model = type(user)
    resource_roles = (
        session.query(resource_model, role_model)
        .join(role_model)
        .filter(role_model.users.any(user_model.id == user.id))
        .order_by(resource_model.id)
        .order_by(role_model.name)
        .all()
    )
    return resource_roles


def get_group_resources_and_roles(session, group, resource_model):
    """Get a group's roles for a all resources of a single resource type"""
    role_model = get_role_model_for_resource_model(resource_model)
    group_model = type(group)
    resource_roles = (
        session.query(resource_model, role_model)
        .join(role_model)
        .filter(role_model.groups.any(group_model.id == group.id))
        .order_by(resource_model.id)
        .order_by(role_model.name)
        .all()
    )
    return resource_roles


def get_user_roles_for_resource(session, user, resource):
    """Get a user's roles for a single resource record"""
    resource_model = type(resource)
    role_model = get_role_model_for_resource_model(resource_model)
    user_model = type(user)
    roles = (
        session.query(role_model)
        .filter(role_model.users.any(user_model.id == user.id))
        .all()
    )
    return roles


# - Get an organization's users and their roles
def get_resource_users_and_roles(session, resource):
    resource_model = type(resource)
    role_model = get_role_model_for_resource_model(resource_model)
    user_model = get_user_model_for_resource_model(resource_model)
    user_roles = (
        session.query(user_model, role_model)
        .select_from(role_model)
        .join(role_model.users)
        .join(resource_model)
        .filter(resource_model.id == resource.id)
        .order_by(user_model.id)
        .order_by(role_model.name)
        .all()
    )
    return user_roles


# - Get all the users who have a specific role
def get_resource_users_with_role(session, resource, role_name):
    resource_model = type(resource)
    role_model = get_role_model_for_resource_model(resource_model)
    user_model = get_user_model_for_resource_model(resource_model)

    users = (
        session.query(user_model)
        .select_from(role_model)
        .join(role_model.users)
        .join(resource_model)
        .filter(role_model.name == role_name, resource_model.id == resource.id)
        .order_by(user_model.id)
        .all()
    )

    return users


# - Assign a user to an organization with a role
def add_user_role(session, user, resource, role_name):
    # TODO: check input for valid role name
    resource_model = type(resource)
    role_model = get_role_model_for_resource_model(resource_model)

    # try to get role
    role = (
        session.query(role_model)
        .select_from(resource_model)
        .join(role_model)
        .filter(resource_model.id == resource.id)
        .filter(role_model.name == role_name)
    ).first()

    if role:
        # TODO: check if user already in role
        role.users.append(user)
    else:
        resource_name = resource_model.__name__.lower()
        kwargs = {"name": role_name, resource_name: resource, "users": [user]}

        role = role_model(**kwargs)
        session.add(role)
        session.commit()


# - Delete a user to an organization with a role
def delete_user_role(session, user, resource, role_name=None):
    resource_model = type(resource)
    role_model = get_role_model_for_resource_model(resource_model)
    user_model = type(user)

    role_query = (
        session.query(role_model)
        .select_from(resource_model)
        .join(role_model)
        .filter(resource_model.id == resource.id)
    )
    if role_name:
        role_query = role_query.filter(role_model.name == role_name)
    else:
        role_query = role_query.filter(role_model.users.any(user_model.id == user.id))

    roles = role_query.all()

    for role in roles:
        try:
            role.users.remove(user)
        except ValueError:
            raise Exception(f"User {user.id} not in role {role.name} for {resource.id}")


# - Change the user's role in an organization
def reassign_user_role(session, user, resource, role_name):
    delete_user_role(session, user, resource)
    add_user_role(session, user, resource, role_name)