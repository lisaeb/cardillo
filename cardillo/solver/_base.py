import numpy as np
from scipy.sparse.linalg import spsolve
from scipy.sparse import coo_matrix, csc_matrix, csr_matrix, bmat


def consistent_initial_conditions(system, rtol=1.0e-5, atol=1.0e-8):
    t0 = system.t0
    q0 = system.q0
    u0 = system.u0

    g_N = system.g_N(t0, q0)
    assert np.all(g_N >= 0)

    I_N = g_N <= 0.0
    g_N_dot = system.g_N_dot(t0, q0, u0)
    B_N = I_N * (g_N_dot <= 0)
    assert np.all(I_N * g_N_dot >= 0) or np.allclose(
        I_N * g_N_dot, np.zeros(system.nla_N), rtol, atol
    ), "Initial conditions do not fulfill g_N_dot0!"

    q_dot0 = system.q_dot(t0, q0, u0)

    M0 = system.M(t0, q0, scipy_matrix=coo_matrix)
    h0 = system.h(t0, q0, u0)
    W_g0 = system.W_g(t0, q0, scipy_matrix=coo_matrix)
    W_gamma0 = system.W_gamma(t0, q0, scipy_matrix=coo_matrix)
    zeta_g0 = system.zeta_g(t0, q0, u0)
    zeta_gamma0 = system.zeta_gamma(t0, q0, u0)

    zeta_N0 = system.g_N_ddot(t0, q0, u0, np.zeros_like(u0))

    la_N0 = system.la_N0
    la_F0 = system.la_F0
    W_N0 = system.W_N(t0, q0, scipy_matrix=csr_matrix)
    W_F0 = system.W_F(t0, q0, scipy_matrix=csr_matrix)
    # fmt: off
    A = bmat(
        [
            [        M0, -W_g0, -W_gamma0],
            [    W_g0.T,  None,      None],
            [W_gamma0.T,  None,      None],
            # [        M0, -W_g0, -W_gamma0, -W_N0[:, B_N]],
            # [    W_g0.T,  None,      None,  None],
            # [W_gamma0.T,  None,      None,  None],
            # [W_N0[:, B_N].T,      None,      None,  None],
        ],
        format="csc",
    )
    b = np.concatenate([
        h0 + W_N0 @ la_N0 + W_F0 @ la_F0, 
        -zeta_g0, 
        -zeta_gamma0,
        # -zeta_N0[B_N],
    ])
    # fmt: on

    u_dot_la_g_la_gamma = spsolve(A, b)
    u_dot0 = u_dot_la_g_la_gamma[: system.nu]
    la_g0 = u_dot_la_g_la_gamma[system.nu : system.nu + system.nla_g]
    la_gamma0 = u_dot_la_g_la_gamma[
        system.nu + system.nla_g : system.nu + system.nla_g + system.nla_gamma
    ]

    # la_N0_ = u_dot_la_g_la_gamma[system.nu + system.nla_g + system.nla_gamma :]
    # la_N0 = np.zeros(system.nla_N)
    # la_N0[I_N] = la_N0_
    la_N0 = system.la_N0

    # check if initial conditions satisfy constraints on position, velocity
    # and acceleration level
    g0 = system.g(t0, q0)
    g_dot0 = system.g_dot(t0, q0, u0)
    g_ddot0 = system.g_ddot(t0, q0, u0, u_dot0)
    gamma0 = system.gamma(t0, q0, u0)
    gamma_dot0 = system.gamma_dot(t0, q0, u0, u_dot0)
    g_S0 = system.g_S(t0, q0)

    g_N_ddot = system.g_N_ddot(t0, q0, u0, u_dot0)
    assert np.all(g_N_ddot >= 0) or np.allclose(
        B_N * g_N_ddot, np.zeros(system.nla_N), rtol, atol
    ), "Initial conditions do not fulfill g_N_ddot0!"
    # assert np.allclose(
    #     B_N * g_N_ddot, np.zeros(system.nla_N), rtol, atol
    # ), "Initial conditions do not fulfill g_N_ddot0!"

    assert np.allclose(
        g0, np.zeros(system.nla_g), rtol, atol
    ), "Initial conditions do not fulfill g0!"
    assert np.allclose(
        g_dot0, np.zeros(system.nla_g), rtol, atol
    ), "Initial conditions do not fulfill g_dot0!"
    assert np.allclose(
        g_ddot0, np.zeros(system.nla_g), rtol, atol
    ), "Initial conditions do not fulfill g_ddot0!"
    assert np.allclose(
        gamma0, np.zeros(system.nla_gamma), rtol, atol
    ), "Initial conditions do not fulfill gamma0!"
    assert np.allclose(
        gamma_dot0, np.zeros(system.nla_gamma), rtol, atol
    ), "Initial conditions do not fulfill gamma_dot0!"
    assert np.allclose(
        g_S0, np.zeros(system.nla_S), rtol, atol
    ), "Initial conditions do not fulfill g_S0!"

    return t0, q0, u0, q_dot0, u_dot0, la_g0, la_gamma0


def compute_I_F(I_N, NF_connectivity):
    """identify active tangent contacts based on active normal contacts and
    NF-connectivity lists"""
    if np.any(I_N):
        I_F = np.array(
            [c for i, I_N_i in enumerate(I_N) for c in NF_connectivity[i] if I_N_i],
            dtype=int,
        )
    else:
        I_F = np.array([], dtype=int)

    return I_F


def constraint_forces(system, t, q, u):
    W_g = system.W_g(t, q, scipy_matrix=csc_matrix)
    W_gamma = system.W_gamma(t, q, scipy_matrix=csc_matrix)
    zeta_g = system.zeta_g(t, q, u)
    zeta_gamma = system.zeta_gamma(t, q, u)
    M = system.M(t, q, scipy_matrix=csc_matrix)
    h = system.h(t, q, u)

    if system.nla_g > 0:
        MW_g = (spsolve(M, W_g)).reshape((system.nu, system.nla_g))
    else:
        MW_g = csc_matrix((system.nu, system.nla_g))
    if system.nla_gamma > 0:
        MW_gamma = (spsolve(M, W_gamma)).reshape((system.nu, system.nla_gamma))
    else:
        MW_gamma = csc_matrix((system.nu, system.nla_gamma))
    Mh = spsolve(M, h)

    # fmt: off
    G = bmat([[    W_g.T @ MW_g,     W_g.T @ MW_gamma], \
                [W_gamma.T @ MW_g, W_gamma.T @ MW_gamma]], format="csc")
    # fmt: on

    mu = np.concatenate(
        (
            zeta_g + W_g.T @ Mh,
            zeta_gamma + W_gamma.T @ Mh,
        )
    )
    la = spsolve(G, -mu)
    la_g = la[: system.nla_g]
    la_gamma = la[system.nla_g :]
    u_dot = spsolve(M, h + W_g @ la_g + W_gamma @ la_gamma)
    return u_dot, la_g, la_gamma
