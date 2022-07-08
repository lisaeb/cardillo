from cardillo.model.frame import Frame
from cardillo.model.bilateral_constraints.implicit import (
    SphericalJoint,
    Linear_guidance_xyz,
)
from cardillo.beams import (
    Rope,
    InflatedRope,
    RopeInternalFluid,
    RopeHydrostaticPressure,
    animate_rope,
)
from cardillo.forces import DistributedForce1D, Force
from cardillo.model import Model
from cardillo.solver import (
    Newton,
    ScipyIVP,
    Riks,
    GenAlphaFirstOrder,
)
from cardillo.math import pi, e1, e2, e3, rodriguez

import numpy as np
import matplotlib.pyplot as plt


def inflated_straight():
    # statics or dynamics?
    statics = True
    # statics = False

    # solver parameter
    if statics:
        atol = 1.0e-8
        rtol = 0.0
        n_load_steps = 10
        max_iter = 20
    else:
        atol = 1.0e-8
        rtol = 1.0e-6
        t1 = 1
        dt = 1.0e-2
        method = "RK45"

    # discretization properties
    nelements = 3
    # polynomial_degree = 1
    # basis = "Lagrange"
    polynomial_degree = 3
    basis = "B-spline"
    # polynomial_degree = 3
    # basis = "Hermite"

    # rope parameters
    g = 9.81
    L = 3.14
    k_e = 1.0e2
    A_rho0 = 1.0e0

    # starting point and corresponding orientation
    r_OP0 = np.zeros(3, dtype=float)
    A_IK0 = np.eye(3, dtype=float)

    # end point
    r_OP1 = L * e1

    # straight initial configuration
    Q = Rope.straight_configuration(
        basis,
        polynomial_degree,
        nelements,
        L,
        r_OP=r_OP0,
        A_IK=A_IK0,
    )

    # Manipulate initial configuration in order to overcome singular initial
    # configuration. Do not change first and last node, otherwise constraints
    # are violated!
    eps = 1.0e-5
    q0 = Q.copy().reshape(-1, 3)
    nn = len(q0)
    for i in range(1, nn - 1):
        q0[i, :2] += eps * 0.5 * (2.0 * np.random.rand(2) - 1)
    q0 = q0.reshape(-1)

    # internal pressure function
    pressure = lambda t: t * 2.0e1

    # build rope class
    rope = InflatedRope(
        pressure,
        k_e,
        polynomial_degree,
        A_rho0,
        nelements,
        Q,
        q0=q0,
        basis=basis,
    )

    # left joint
    frame1 = Frame(r_OP=r_OP0, A_IK=A_IK0)
    joint1 = SphericalJoint(frame1, rope, r_OP0, frame_ID2=(0,))

    # left joint
    frame2 = Frame(r_OP=r_OP1, A_IK=A_IK0)
    joint2 = SphericalJoint(frame2, rope, r_OP1, frame_ID2=(1,))

    __fg = -A_rho0 * g * e2
    if statics:
        fg = lambda t, xi: t * __fg
    else:
        fg = lambda t, xi: __fg
    gravity = DistributedForce1D(fg, rope)

    # assemble the model
    model = Model()
    model.add(rope)
    model.add(frame1)
    model.add(joint1)
    model.add(frame2)
    model.add(joint2)
    # model.add(gravity)
    model.assemble()

    if statics:
        solver = Newton(
            model,
            n_load_steps=n_load_steps,
            max_iter=max_iter,
            atol=atol,
            rtol=rtol,
        )
    else:
        solver = ScipyIVP(
            model,
            t1,
            dt,
            method=method,
            rtol=rtol,
            atol=atol,
        )

    sol = solver.solve()
    q = sol.q
    nt = len(q)
    t = sol.t[:nt]

    animate_rope(t, q, [rope], L, show=True)


def inflated_quarter_circle():
    # statics or dynamics?
    # statics = True
    statics = False

    # solver parameter
    if statics:
        atol = 1.0e-8
        rtol = 0.0
        n_load_steps = 10
        max_iter = 20
    else:
        atol = 1.0e-8
        rtol = 1.0e-6
        t1 = 1
        dt = 1.0e-2
        method = "RK45"

    # discretization properties
    nelements = 5
    # polynomial_degree = 1
    # basis = "Lagrange"
    polynomial_degree = 3
    basis = "B-spline"
    # polynomial_degree = 3
    # basis = "Hermite"

    # rope parameters
    R = 1
    rho_g = 2.0e1
    k_e = 1.0e3
    A_rho0 = 1.0e0

    # internal pressure function
    pressure = lambda t: t * 2.0e1

    # straight initial configuration
    Q = Rope.quarter_circle_configuration(
        basis,
        polynomial_degree,
        nelements,
        R,
    )

    # Manipulate initial configuration in order to overcome singular initial
    # configuration. Do not change first and last node, otherwise constraints
    # are violated!
    eps = 1.0e-7
    q0 = Q.copy().reshape(-1, 3)
    nn = len(q0)
    for i in range(1, nn - 1):
        q0[i] += eps * np.array([1, 1, 0], dtype=float)
    q0 = q0.reshape(-1)

    # build rope class
    # rope = InflatedRope(
    #     pressure,
    rope = RopeHydrostaticPressure(
        rho_g,
        k_e,
        polynomial_degree,
        A_rho0,
        nelements,
        Q,
        q0=q0,
        basis=basis,
    )

    # left joint
    r_OP0 = Q.reshape(-1, 3)[0]
    A_IK0 = rodriguez(pi / 2 * e3)
    frame0 = Frame(r_OP=r_OP0, A_IK=A_IK0)
    # joint0 = SphericalJoint(frame0, rope, r_OP0, frame_ID2=(0,))
    joint0 = Linear_guidance_xyz(frame0, rope, r_OP0, A_IK0, frame_ID2=(0,))

    # left joint
    r_OP1 = Q.reshape(-1, 3)[-1]
    A_IK1 = np.eye(3, dtype=float)
    frame1 = Frame(r_OP=r_OP1, A_IK=A_IK1)
    joint1 = SphericalJoint(frame1, rope, r_OP1, frame_ID2=(1,))
    # joint1 = Linear_guidance_xyz(frame1, rope, r_OP1, A_IK1, frame_ID2=(1,))

    # assemble the model
    model = Model()
    model.add(rope)
    model.add(frame0)
    model.add(joint0)
    model.add(frame1)
    model.add(joint1)
    model.assemble()

    if statics:
        solver = Newton(
            model,
            n_load_steps=n_load_steps,
            max_iter=max_iter,
            atol=atol,
            rtol=rtol,
        )
    else:
        solver = ScipyIVP(
            model,
            t1,
            dt,
            method=method,
            rtol=rtol,
            atol=atol,
        )

    sol = solver.solve()
    q = sol.q
    nt = len(q)
    t = sol.t[:nt]

    # ratio of rope initial and deformed length
    r = rope.r_OP(1, q[-1][rope.qDOF_P((1,))], (1,))[0]
    l = 2 * pi * r
    L = 2 * pi * R
    print(f"l / L: {l / L}")

    # analytical stretch
    la_analytic = pressure(1) * r / k_e + 1
    print(f"analytical stretch: {la_analytic}")

    # initial vs. current area
    A = rope.area(q[0])
    a = rope.area(q[-1])
    A_analytic = np.pi * R**2 / 4
    a_analytic = np.pi * r**2 / 4
    print(f"A: {A}")
    print(f"a: {a}")
    print(f"A analytic: {A_analytic}")
    print(f"a analytic: {a_analytic}")

    # # stretch of the final configuration
    # n = 100
    # xis = np.linspace(0, 1, num=n)
    # la = rope.stretch(q[-1])
    # # print(f"la: {la}")
    # fig, ax = plt.subplots()
    # ax.plot(xis, la)
    # ax.set_ylim(0, 2)
    # ax.grid()

    animate_rope(t, q, [rope], R, show=True)


def inflated_quarter_circle_external_force():
    # statics or dynamics?
    statics = True
    # statics = False

    # solver parameter
    if statics:
        atol = 1.0e-8
        rtol = 0.0
        n_load_steps = 10
        max_iter = 20
    else:
        atol = 1.0e-8
        rtol = 1.0e-6
        t1 = 1
        dt = 1.0e-2
        method = "RK45"

    # discretization properties
    nelements = 5
    # polynomial_degree = 1
    # basis = "Lagrange"
    polynomial_degree = 3
    basis = "B-spline"
    # polynomial_degree = 3
    # basis = "Hermite"

    # rope parameters
    R = 1
    k_e = 1.0e5
    k_a = 1.0e5
    A_rho0 = 1.0e0

    # internal pressure function
    pressure = lambda t: t * 1.0e1

    # straight initial configuration
    Q = Rope.quarter_circle_configuration(
        basis,
        polynomial_degree,
        nelements,
        R,
    )

    # Manipulate initial configuration in order to overcome singular initial
    # configuration. Do not change first and last node, otherwise constraints
    # are violated!
    eps = 1.0e-6
    q0 = Q.copy().reshape(-1, 3)
    nn = len(q0)
    for i in range(1, nn - 1):
        q0[i] += eps * np.array([1, 1, 0], dtype=float)
    q0 = q0.reshape(-1)

    # build rope class
    # rope = InflatedRope(
    #     pressure,
    rope = RopeInternalFluid(
        k_a,
        k_e,
        polynomial_degree,
        A_rho0,
        nelements,
        Q,
        q0=q0,
        basis=basis,
    )

    # left joint
    r_OP0 = Q.reshape(-1, 3)[0]
    A_IK0 = rodriguez(pi / 2 * e3)
    frame0 = Frame(r_OP=r_OP0, A_IK=A_IK0)
    # joint0 = SphericalJoint(frame0, rope, r_OP0, frame_ID2=(0,))
    joint0 = Linear_guidance_xyz(frame0, rope, r_OP0, A_IK0, frame_ID2=(0,))

    # left joint
    r_OP1 = Q.reshape(-1, 3)[-1]
    A_IK1 = np.eye(3, dtype=float)
    frame1 = Frame(r_OP=r_OP1, A_IK=A_IK1)
    # joint1 = SphericalJoint(frame1, rope, r_OP1, frame_ID2=(1,))
    joint1 = Linear_guidance_xyz(frame1, rope, r_OP1, A_IK1, frame_ID2=(1,))

    f = lambda t: t * e1 * 1.0e0
    force = Force(f, rope, frame_ID=(1,))

    # __fg = -A_rho0 * g * e2
    # if statics:
    #     fg = lambda t, xi: t * __fg
    # else:
    #     fg = lambda t, xi: __fg
    # gravity = DistributedForce1D(fg, rope)

    # assemble the model
    model = Model()
    model.add(rope)
    model.add(frame0)
    model.add(joint0)
    model.add(frame1)
    model.add(joint1)
    # model.add(gravity)
    model.add(force)
    model.assemble()

    if statics:
        solver = Newton(
            model,
            n_load_steps=n_load_steps,
            max_iter=max_iter,
            atol=atol,
            rtol=rtol,
        )
    else:
        solver = ScipyIVP(
            model,
            t1,
            dt,
            method=method,
            rtol=rtol,
            atol=atol,
        )

    sol = solver.solve()
    q = sol.q
    nt = len(q)
    t = sol.t[:nt]

    # ratio of rope initial and deformed length
    r = rope.r_OP(1, q[-1][rope.qDOF_P((1,))], (1,))[0]
    l = 2 * pi * r
    L = 2 * pi * R
    print(f"l / L: {l / L}")

    # analytical stretch
    la_analytic = pressure(1) * r / k_e + 1
    print(f"analytical stretch: {la_analytic}")

    # initial vs. current area
    A = rope.area(q[0])
    a = rope.area(q[-1])
    A_analytic = np.pi * R**2 / 4
    a_analytic = np.pi * r**2 / 4
    print(f"A: {A}")
    print(f"a: {a}")
    print(f"A analytic: {A_analytic}")
    print(f"a analytic: {a_analytic}")

    # stretch of the final configuration
    n = 100
    xis = np.linspace(0, 1, num=n)
    la = rope.stretch(q[-1])
    # print(f"la: {la}")
    fig, ax = plt.subplots()
    ax.plot(xis, la)
    ax.set_ylim(0, 2)
    ax.grid()

    animate_rope(t, q, [rope], R, show=True)


def inflated_circular_segment():
    # statics or dynamics?
    statics = True
    # statics = False

    # solver parameter
    if statics:
        atol = 1.0e-8
        rtol = 0.0
        n_load_steps = 10
        max_iter = 40
    else:
        atol = 1.0e-6
        rtol = 1.0e-6
        t1 = 1
        dt = 5.0e-3
        method = "RK45"
        # method = "RK23"

    # discretization properties
    nelements = 10
    # polynomial_degree = 2
    # basis = "Lagrange"
    polynomial_degree = 2
    basis = "B-spline"
    # polynomial_degree = 3
    # basis = "Hermite"

    # rope parameters
    g = 9.81
    R = 1
    # phi = np.pi / 6
    phi = np.pi / 4
    # k_e = 1.0e4
    # k_a = 1.0e4
    k_e = 1.0e6
    k_a = 1.0e6
    A_rho0_inertia = 1.0e2
    if statics:
        A_rho0_gravity = 5.0e1
    else:
        A_rho0_gravity = 5.0e1

    # straight initial configuration
    Q = Rope.circular_segment_configuration(
        basis,
        polynomial_degree,
        nelements,
        R,
        phi,
    )

    # hydrostatic pressure
    rho_g = 1.0e0
    h0 = Q.reshape(-1, 3)[0][0]

    # Manipulate initial configuration in order to overcome singular initial
    # configuration. Do not change first and last node, otherwise constraints
    # are violated!
    if statics:
        eps = 1.0e-7
        q0 = Q.copy().reshape(-1, 3)
        nn = len(q0)
        for i in range(1, nn - 1):
            # q0[i] += eps * np.array([1, 1, 0], dtype=float)
            q0[i, :2] += eps * 0.5 * (2.0 * np.random.rand(2) - 1)
        q0 = q0.reshape(-1)
    else:
        q0 = Q.copy()

    # build rope class
    # rope = RopeHydrostaticPressure(
    #     rho_g,
    #     h0,
    #     k_e,
    #     polynomial_degree,
    #     A_rho0_inertia,
    #     nelements,
    #     Q,
    #     q0=q0,
    #     basis=basis,
    # )
    rope = RopeInternalFluid(
        rho_g,
        h0,
        k_a,
        k_e,
        polynomial_degree,
        A_rho0_inertia,
        nelements,
        Q,
        q0=q0,
        basis=basis,
    )

    # left joint
    r_OP0 = Q.reshape(-1, 3)[0]
    A_IK0 = rodriguez(pi / 2 * e3)
    frame0 = Frame(r_OP=r_OP0, A_IK=A_IK0)
    joint0 = SphericalJoint(frame0, rope, r_OP0, frame_ID2=(0,))

    # left joint
    r_OP1 = Q.reshape(-1, 3)[-1]
    A_IK1 = np.eye(3, dtype=float)
    frame1 = Frame(r_OP=r_OP1, A_IK=A_IK1)
    # joint1 = SphericalJoint(frame1, rope, r_OP1, frame_ID2=(1,))
    joint1 = Linear_guidance_xyz(frame1, rope, r_OP1, A_IK1, frame_ID2=(1,))

    # r_OP1 = lambda t: Q.reshape(-1, 3)[-1] + t * e1 * 0.01
    # A_IK1 = np.eye(3, dtype=float)
    # frame1 = Frame(r_OP=r_OP1, A_IK=A_IK1)
    # joint1 = SphericalJoint(frame1, rope, r_OP1(0), frame_ID2=(1,))

    # # dispalcement of node
    # if statics:
    #     r_OP2 = lambda t: Q.reshape(-1, 3)[1] - max(0, t - 1.0e-6) * 0.01 * e2 * R
    # else:
    #     r_OP2 = lambda t: Q.reshape(-1, 3)[1] - max(0, t / t1 - 1.0e-6) * 0.1 * e2 * R
    # A_IK2 = np.eye(3, dtype=float)
    # frame2 = Frame(r_OP=r_OP2, A_IK=A_IK2)
    # joint2 = SphericalJoint(frame2, rope, r_OP2(0), frame_ID2=(1.0 / (rope.nnode - 1),))

    if statics:

        def fg(t, xi, xi_star=0.3, t_star=0.5):
            if xi <= xi_star and t > t_star:
                return -(t - t_star) / (1 - t_star) * A_rho0_gravity * g * e2
            # if xi <= xi_star:
            #     return -t * A_rho0_gravity * g * e2
            else:
                return np.zeros(3, dtype=float)

    else:

        def fg(t, xi, xi_star=0.3):
            if xi <= xi_star:
                return -A_rho0_gravity * g * e2
            else:
                return np.zeros(3, dtype=float)

    gravity = DistributedForce1D(fg, rope)

    __f = 1.0e1 * e1
    if statics:
        f = lambda t: t * __f
    else:
        f = lambda t: __f
    force = Force(f, rope, frame_ID=(1,))

    # assemble the model
    model = Model()
    model.add(rope)
    model.add(frame0)
    model.add(joint0)
    model.add(frame1)
    model.add(joint1)
    # model.add(frame2)
    # model.add(joint2)
    # model.add(gravity)
    # model.add(force)
    model.assemble()

    if statics:
        solver = Newton(
            model,
            n_load_steps=n_load_steps,
            max_iter=max_iter,
            atol=atol,
            rtol=rtol,
        )
        # solver = Riks(
        #     model,
        #     tol=atol,
        #     max_newton_iter=max_iter,
        #     # la_arc0=5.0e-3, # works for 5 cubic B-spline elements
        #     la_arc0=1.0e-3,
        #     la_arc_span=[-1, 1],
        # )
    else:
        # solver = ScipyIVP(
        #     model,
        #     t1,
        #     dt,
        #     method=method,
        #     rtol=rtol,
        #     atol=atol,
        # )
        solver = GenAlphaFirstOrder(
            model,
            t1,
            dt,
            rho_inf=0.5,
            tol=atol,
        )

    sol = solver.solve()
    q = sol.q
    nt = len(q)
    t = sol.t[:nt]

    # ratio of rope initial and deformed length
    r = rope.r_OP(1, q[-1][rope.qDOF_P((1,))], (1,))[0]
    l = 2 * pi * r
    L = 2 * pi * R
    print(f"l / L: {l / L}")

    # initial vs. current area
    A = rope.area(q[0])
    a = rope.area(q[-1])
    print(f"A: {A}")
    print(f"a: {a}")

    # stretch of the final configuration
    n = 100
    xis = np.linspace(0, 1, num=n)
    la = rope.stretch(q[-1])
    # print(f"la: {la}")
    fig, ax = plt.subplots()
    ax.plot(xis, la)
    ax.set_ylim(0, 2)
    ax.grid()

    animate_rope(t, q, [rope], R, show=True)


if __name__ == "__main__":
    # inflated_straight()
    # inflated_quarter_circle()
    # inflated_quarter_circle_external_force()
    inflated_circular_segment()
