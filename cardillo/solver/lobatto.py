import numpy as np
from tqdm import tqdm

from cardillo.math import fsolve, prox_R0_np, prox_sphere
from cardillo.solver._butcher_tableaus import LobattoIIIATableau, LobattoIIIBTableau
from cardillo.solver import Solution, consistent_initial_conditions


class LobattoIIIAB:
    def __init__(
        self,
        system,
        t1,
        dt,
        stages=2,  # the standard value is Rattle
        atol=1e-6,
        max_iter=20,
    ):
        self.system = system

        # butcher tableaus
        self.stages = stages
        self.tableau_IIIA = LobattoIIIATableau(stages=stages)
        self.A = self.tableau_IIIA.A
        self.b = self.tableau_IIIA.b
        self.c = self.tableau_IIIA.c
        self.tableau_IIIB = LobattoIIIBTableau(stages=stages)
        self.A_hat = self.tableau_IIIB.A
        self.b_hat = self.tableau_IIIB.b
        self.c_hat = self.tableau_IIIB.c

        # integration time
        self.t0 = t0 = system.t0
        self.t1 = (
            t1 if t1 > t0 else ValueError("t1 must be larger than initial time t0.")
        )
        self.dt = dt
        self.t = np.arange(t0, self.t1 + self.dt, self.dt)

        # convergence criteria
        self.atol = atol
        self.max_iter = max_iter

        # dimensions
        self.nq = self.system.nq
        self.nu = self.system.nu
        self.nla_g = self.system.nla_g
        self.nla_gamma = self.system.nla_gamma
        self.nla_N = self.system.nla_N
        self.nla_F = self.system.nla_F

        # consistent initial conditions
        (
            self.tn,
            self.qn,
            self.un,
            self.q_dotn,
            self.u_dotn,
            self.la_gn,
            self.la_gamman,
            self.la_Nn,
            self.la_Fn,
        ) = consistent_initial_conditions(system)

        self.R_gn = self.la_gn
        self.R_gamman = self.la_gamman
        self.P_Nn = self.la_Nn
        self.P_Fn = self.la_Fn
        self.I = np.zeros((stages, self.nla_N), dtype=bool)

        self.yn = np.concatenate(
            (
                np.tile(self.un, stages),
                self.un,
                # np.tile(self.la_gn, stages),
                # np.tile(self.la_gamman, stages),
                np.tile(system.la_N0, stages),
                np.tile(system.la_F0, stages),
            )
        )

        self.split_y = np.cumsum(
            np.array(
                [
                    self.nu * stages,
                    self.nu,
                    self.nla_N * stages,
                ],
                dtype=int,
            )
        )

    def unpack_y(self, y):
        U_list, u, R_N_list, R_F_list = np.array_split(y, self.split_y)
        U = U_list.reshape(self.stages, self.nu, order="F")
        R_N = R_N_list.reshape(self.stages, self.nla_N, order="F")
        R_F = R_F_list.reshape(self.stages, self.nla_F, order="F")
        return U, u, R_N, R_F

    def R(self, y, update_indexset=True):
        # time step
        dt = self.dt

        # quantities from previous step
        tn = self.tn
        qn = self.qn
        un = self.un

        # unpack quantities
        U, un1, R_N, R_F = self.unpack_y(y)

        # this scaling fixes problem of the singular jacobian. It appears to be a conditioning problem.
        # With that scaling, the Lagrange multipliers in the eqm. and in the constraints have the same order of magnitude.
        # R_N = R_N / dt
        # V = V * dt

        # compute position update
        # Q = np.tile(qn, (self.stages, 1)).T + dt * U @ self.A.T
        Q = np.array([qn + dt * self.system.q_dot(tn, qn, Ui) for Ui in self.A @ U])
        qn1 = Q[-1]

        # compute momenta, forces and percussions
        Pi = np.zeros((self.stages, self.nu))
        F = np.zeros((self.stages, self.nu))
        R2 = np.zeros((self.stages, self.nla_N))
        R3 = np.zeros((self.stages, self.nla_F))

        tn1 = tn + dt
        pin = self.system.M(tn, qn) @ un
        pi = self.system.M(tn1, qn1) @ un1
        # P_N = dt * self.A_hat @ R_N
        p_N = dt * self.b_hat @ R_N

        # P_F = dt * self.A_hat @ R_F
        p_F = dt * self.b_hat @ R_F

        # store for next time step
        self.tn1 = tn1
        self.qn1 = qn1
        self.un1 = un1
        self.P_Nn1 = p_N
        self.P_Fn1 = p_F

        # save r-parameter
        prox_r_N = self.prox_r_N
        prox_r_F = self.prox_r_F

        # friction coefficients
        mu = self.system.mu

        # connectivity matrix of normal force directions and friction force directions
        NF_connectivity = self.system.NF_connectivity

        for i in range(self.stages):
            ti = tn + self.c[i] * dt
            Pi[i] = self.system.M(ti, Q[i]) @ U[i]
            F[i] = (
                self.system.h(ti, Q[i], U[i])
                # + self.system.W_g(ti, Q[i]) @ R_g[i]
                # + self.system.W_gamma(ti, Q[i]) @ R_gamma[i]
                + self.system.W_N(ti, Q[i]) @ R_N[i]
                + self.system.W_F(ti, Q[i]) @ R_F[i]
            )
            if i > 0:
                prox_arg = self.system.g_N(ti, Q[i]) - prox_r_N * R_N[i - 1]
                if update_indexset:
                    self.I[i] = prox_arg <= 0
                R2[i - 1] = np.where(
                    self.I[i],
                    self.system.g_N(ti, Q[i]),
                    R_N[i - 1],
                )
                gamma_F = self.system.gamma_F(ti, Q[i], U[i])
                for i_N, i_F in enumerate(NF_connectivity):
                    i_F = np.array(i_F)
                    R3[i - 1, i_F] = R_F[i - 1, i_F] + prox_sphere(
                        prox_r_F[i_N] * gamma_F[i_F] - R_F[i - 1, i_F],
                        mu[i_N] * R_N[i - 1, i_N],
                    )

        xi_N = self.system.xi_N(ti, qn1, un, un1)
        xi_F = self.system.xi_F(ti, qn1, un, un1)

        R2[-1] = np.where(
            # TODO: What dof you prefer?
            # self.I[-1],
            np.any(self.I, axis=0),
            xi_N - prox_R0_np(xi_N - prox_r_N * p_N),
            p_N,
        )
        for i_N, i_F in enumerate(NF_connectivity):
            i_F = np.array(i_F)
            R3[-1, i_F] = p_F[i_F] + prox_sphere(
                prox_r_F[i_N] * xi_F[i_F] - p_F[i_F],
                mu[i_N] * p_N[i_N],
            )

        # R1 = Pi - (np.tile(pin, (self.stages, 1)).T + dt * F @ self.A_hat.T)
        R1 = Pi - np.array([pin + dt * Fi for Fi in self.A_hat @ F])
        r1 = pi - (pin + dt * self.b_hat @ F)

        return np.concatenate(
            (R1.flatten(order="F"), r1, R2.flatten(order="F"), R3.flatten(order="F"))
        )

    def solve(self):

        # lists storing output variables
        q = [self.qn]
        u = [self.un]
        # la_g = [self.R_gn]
        # la_gamma = [self.R_gamman]
        P_N = [self.P_Nn]
        P_F = [self.P_Fn]

        pbar = tqdm(self.t[:-1])
        for _ in pbar:
            # only compute optimized proxparameters once per time step
            self.prox_r_N = self.system.prox_r_N(self.tn, self.qn)
            self.prox_r_F = self.system.prox_r_F(self.tn, self.qn)

            yn1, converged, error, i, _ = fsolve(
                self.R,
                self.yn,
                jac="2-point",
                eps=1.0e-6,
                atol=self.atol,
                fun_args=(True,),
                jac_args=(False,),
                max_iter=self.max_iter,
            )

            self.yn = yn1.copy()

            # U, un1, R_N, R_F = self.unpack_y(yn1)
            # # R_N = R_N / self.dt
            # # V = V * self.dt
            # tn1 = self.tn + self.dt
            # # TODO: Kinematic equation
            # qn1 = self.qn + self.dt * U @ self.b
            # P_Nn1 = self.dt * R_N @ self.b_hat
            # P_Fn1 = self.dt * R_F @ self.b_hat

            pbar.set_description(
                f"t: {self.tn1:0.2e}; iterations: {i+1}; error: {error:.3e}"
            )
            if not converged:
                raise RuntimeError(
                    f"iteration not converged after {i+1} iterations with error: {error:.5e}"
                )

            self.qn1, self.un1 = self.system.step_callback(self.tn1, self.qn1, self.un1)

            q.append(self.qn1)
            u.append(self.un1)
            # la_g.append(self.P_gn1)
            # la_gamma.append(self.P_gamman1)
            P_N.append(self.P_Nn1)
            P_F.append(self.P_Fn1)

            # update local variables for accepted time step
            (
                self.tn,
                self.qn,
                self.un,
                # self.la_gk,
                # self.la_gammak,
                self.P_Nn,
                self.P_Fn,
            ) = (self.tn1, self.qn1, self.un1, self.P_Nn1, self.P_Fn1)

        return Solution(
            t=np.array(self.t),
            q=np.array(q),
            u=np.array(u),
            # la_g=np.array(la_g),
            # la_gamma=np.array(la_gamma),
            P_N=np.array(P_N),
            P_F=np.array(P_F),
        )
