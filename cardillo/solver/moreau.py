import numpy as np
from scipy.sparse.linalg import spsolve
from scipy.sparse import csc_matrix, bmat, eye
from tqdm import tqdm

from cardillo.math.numerical_derivative import Numerical_derivative
from cardillo.solver import Solution
from cardillo.math.prox import prox_Rn0, prox_circle
from cardillo.math.algebra import norm2

class Moreau():
    def __init__(self, model, t1, dt, fix_point_tol=1e-8, fix_point_max_iter=1000, prox_solver_method='fixed-point', newton_tol=1e-6, newton_max_iter=50, error_function=lambda x: np.max(np.abs(x))):
        self.model = model

        # integration time
        t0 = model.t0
        self.t1 = t1 if t1 > t0 else ValueError("t1 must be larger than initial time t0.")
        self.dt = dt
        self.t = np.arange(t0, self.t1 + self.dt, self.dt)

        self.fix_point_error_function = error_function
        self.fix_point_tol = fix_point_tol
        self.fix_point_max_iter = fix_point_max_iter

        self.newton_tol = newton_tol
        self.newton_max_iter = newton_max_iter
        self.newton_error_function = error_function

        self.nq = self.model.nq
        self.nu = self.model.nu
        self.nla_g = self.model.nla_g
        self.nla_gamma = self.model.nla_gamma
        self.nla_N = self.model.nla_N
        self.nla_T = self.model.nla_T
        self.nR_smooth = self.nu + self.nla_g + self.nla_gamma
        self.nR = self.nR_smooth + self.nla_N + self.nla_T

        self.tk = model.t0
        self.qk = model.q0 
        self.uk = model.u0 
        self.la_gk = model.la_g0
        self.la_gammak = model.la_gamma0
        self.P_Nk = model.la_N0 * dt
        self.P_Tk = model.la_T0 * dt

        # TODO:
        self.NT_connectivity = self.model.NT_connectivity

        self.DOFs_smooth = np.arange(self.nR_smooth)

        if prox_solver_method == 'fixed-point':
            self.step = self.__step_fixed_point
        elif prox_solver_method == 'newton':
            self.step = self.__step_newton

    def __step_fixed_point(self):
        # general quantities
        dt = self.dt
        uk = self.uk
        tk1 = self.tk + dt
        qk1 = self.qk + dt * self.model.q_dot(self.tk, self.qk, uk)

        M = self.model.M(tk1, qk1)
        h = self.model.h(tk1, qk1, uk)
        W_g = self.model.W_g(tk1, qk1)
        W_gamma = self.model.W_gamma(tk1, qk1)
        W_N = self.model.W_N(tk1, qk1, scipy_matrix=csc_matrix)
        W_T = self.model.W_T(tk1, qk1, scipy_matrix=csc_matrix)
        g_dot_u = self.model.g_dot_u(tk1, qk1)
        chi_g = self.model.chi_g(tk1, qk1)
        gamma_u = self.model.gamma_u(tk1, qk1)
        chi_gamma = self.model.chi_gamma(tk1, qk1)

        # identify active normal and tangential contacts
        g_N = self.model.g_N(tk1, qk1)
        I_N = (g_N <= 0)
        if np.any(I_N):
            I_T = np.array([c for i, I_N_i in enumerate(I_N) for c in self.model.NT_connectivity[i] if I_N_i], dtype=int)
        else:
            I_T = np.array([], dtype=int)

        # solve for new velocities and bilateral constraint forces
        # M (uk1 - uk) - dt (h + W_g la_g + W_gamma la_gamma + W_gN la_N + W_gT la_T) = 0
        # g_dot_u @ uk1 + chi_g = 0
        # gamma_u @ uk1 + chi_gamma = 0
        A =  bmat([[M      ,  -dt * W_g, -dt * W_gamma], \
                   [g_dot_u,       None,          None], \
                   [gamma_u,       None,          None]]).tocsc()

        b = np.concatenate( (M @ uk + dt * h + W_N[:, I_N] @ self.P_Nk[I_N] + W_T[:, I_T] @ self.P_Tk[I_T],\
                             -chi_g,\
                             -chi_gamma) )

        x = spsolve(A, b)
        uk1 = x[:self.nu]
        la_gk1 = x[self.nu:self.nu+self.nla_g]
        la_gammak1 = x[self.nu+self.nla_g:]

        P_Nk1 = np.zeros(self.nla_N)
        P_Tk1 = np.zeros(self.nla_T)

        converged = True
        error = 0
        j = 0
        if np.any(I_N):
            converged = False
            P_Nk1_i = self.P_Nk.copy()
            P_Nk1_i1 = self.P_Nk.copy()
            P_Tk1_i = self.P_Tk.copy()
            P_Tk1_i1 = self.P_Tk.copy()
            for j in range(self.fix_point_max_iter):
                
                # relative contact velocity and prox equation
                P_Nk1_i1[I_N] = prox_Rn0(P_Nk1_i[I_N] - self.model.prox_r_N[I_N] * self.model.xi_N(tk1, qk1, uk, uk1)[I_N])

                xi_T = self.model.xi_T(tk1, qk1, uk, uk1)
                for i_N, i_T in enumerate(self.NT_connectivity):
                    if I_N[i_N] and len(i_T):
                        P_Tk1_i1[i_T] = prox_circle(P_Tk1_i[i_T] - self.model.prox_r_T[i_N] * xi_T[i_T], self.model.mu[i_N] * P_Nk1_i1[i_N]) 

                # check if velocities or contact percussions do not change
                # error = self.fix_point_error_function(uk1 - uk0)
                R = np.concatenate( (P_Nk1_i1[I_N] - P_Nk1_i[I_N], P_Tk1_i1[I_T] - P_Tk1_i[I_T]) )
                error = self.fix_point_error_function(R)
                converged = error < self.fix_point_tol
                if converged:
                    P_Nk1[I_N] = P_Nk1_i1[I_N]
                    P_Tk1[I_T] = P_Tk1_i1[I_T]
                    break
                P_Nk1_i = P_Nk1_i1.copy()
                P_Tk1_i = P_Tk1_i1.copy()

                # solve for new velocities and bilateral constraint forces
                A =  bmat([[M      ,  -dt * W_g, -dt * W_gamma], \
                        [g_dot_u,       None,          None], \
                        [gamma_u,       None,          None]]).tocsr()

                b = np.concatenate( (M @ uk + dt * h + W_N[:, I_N] @ P_Nk1_i[I_N] + W_T[:, I_T] @ P_Tk1_i[I_T],\
                                     -chi_g,\
                                     -chi_gamma) )

                x = spsolve(A, b)
                uk1 = x[:self.nu]
                la_gk1 = x[self.nu:self.nu+self.nla_g]
                la_gammak1 = x[self.nu+self.nla_g:]
                
        return (converged, j, error), tk1, qk1, uk1, la_gk1, la_gammak1, P_Nk1, P_Tk1

    def __step_newton(self):
        # general quantities
        dt = self.dt
        uk = self.uk
        tk1 = self.tk + dt
        self.qk1 = qk1 = self.qk + dt * self.model.q_dot(self.tk, self.qk, uk)

        # initial residual and error
        R = np.zeros(self.nR)
        uk1 = uk.copy() 
        la_gk1 = self.la_gk.copy()
        la_gammak1 = self.la_gammak.copy()
        P_Nk1 = self.P_Nk.copy()
        P_Tk1 = self.P_Tk.copy()
        # P_Nk1 = np.zeros_like(self.la_Nk)
        # P_Tk1 = np.zeros_like(self.la_Tk)
        xk1 = np.concatenate( (uk1, la_gk1, la_gammak1, P_Nk1, P_Tk1) )

        # identify active normal and tangential contacts
        g_N = self.model.g_N(tk1, qk1)
        self.I_N = I_N = (g_N <= 0)
        if np.any(I_N):
            self.I_T = np.array([c for i, I_N_i in enumerate(I_N) for c in self.model.NT_connectivity[i] if I_N_i], dtype=int)
        else:
            self.I_T = np.array([], dtype=int)

        # compute residual
        R = self.__R_newton(tk1, xk1)
        error = self.newton_error_function(R)
        converged = error < self.newton_tol 
        j = 0
        if not converged:
            for j in range(self.newton_max_iter):
                # jacobian
                R_x = Numerical_derivative(self.__R_newton, order=2)._x(tk1, xk1)

                # Newton update
                dx = spsolve(R_x, R)
                # try:
                #     dx = spsolve(R_x, R)
                # except:
                #     R = self.__R_newton(tk1, xk1)
                
                xk1 -= dx
                uk1 = xk1[:self.nu]
                la_gk1 = xk1[self.nu:self.nu+self.nla_g]
                la_gammak1 = xk1[self.nu+self.nla_g:self.nu+self.nla_g+self.nla_gamma]
                P_Nk1[I_N] = xk1[self.nR_smooth:self.nR_smooth+np.count_nonzero(I_N)]
                P_Nk1[~I_N] = xk1[self.nR_smooth+np.count_nonzero(I_N):self.nR_smooth+self.nla_N]
                offset = 0
                for i_T in self.NT_connectivity:
                    nT = len(i_T)
                    if nT:
                        P_Tk1[i_T] = xk1[self.nR_smooth+self.nla_N+offset:self.nR_smooth+self.nla_N+offset+nT]
                        offset += nT
                
                R = self.__R_newton(tk1, xk1)
                error = self.newton_error_function(R)
                converged = error < self.newton_tol
                if converged:
                    break
        
        return (converged, j, error), tk1, qk1, uk1, la_gk1, la_gammak1, P_Nk1, P_Tk1

    def __R_newton(self, tk1, xk1):
        dt = self.dt
        uk = self.uk
        qk1 = self.qk1
        I_T = self.I_T
        I_N = self.I_N
        nI_N = np.count_nonzero(I_N)

        uk1 = xk1[:self.nu]
        la_gk1 = xk1[self.nu:self.nu+self.nla_g]
        la_gammak1 = xk1[self.nu+self.nla_g:self.nu+self.nla_g+self.nla_gamma]
        P_Nk1 = np.zeros(self.nla_N)
        P_Nk1[I_N] = xk1[self.nR_smooth:self.nR_smooth+nI_N]
        P_Nk1[~I_N] = xk1[self.nR_smooth+nI_N:self.nR_smooth+self.nla_N]
        P_Tk1 = xk1[self.nR_smooth+self.nla_N:]

        M = self.model.M(tk1, qk1)
        h = self.model.h(tk1, qk1, uk)
        W_g = self.model.W_g(tk1, qk1)
        W_gamma = self.model.W_gamma(tk1, qk1)
        W_N = self.model.W_N(tk1, qk1, scipy_matrix=csc_matrix)
        W_T = self.model.W_T(tk1, qk1, scipy_matrix=csc_matrix)
        xi_N = self.model.xi_N(tk1, qk1, uk, uk1)
        xi_T = self.model.xi_T(tk1, qk1, uk, uk1)

        R = np.zeros(self.nR)
        R[:self.nu] = M @ (uk1 - uk) - dt * (h + W_g @ la_gk1 + W_gamma @ la_gammak1) - W_N @ P_Nk1 - W_T @ P_Tk1
        R[self.nu:self.nu+self.nla_g] = self.model.g_dot(tk1, qk1, uk1)
        R[self.nu+self.nla_g:self.nu+self.nla_g+self.nla_gamma] = self.model.gamma(tk1, qk1, uk1)
        R[self.nR_smooth:self.nR_smooth+nI_N] = P_Nk1[I_N] - prox_Rn0(P_Nk1[I_N] - self.model.prox_r_N[I_N] * xi_N[I_N])
        R[self.nR_smooth+nI_N:self.nR_smooth+self.nla_N] = P_Nk1[~I_N]

        offset = 0
        for i_N, i_T in enumerate(self.NT_connectivity):
            nT = len(i_T)
            if nT:
                if self.I_N[i_N]:
                    R[self.nR_smooth+self.nla_N+offset:self.nR_smooth+self.nla_N+offset+nT] = P_Tk1[i_T] - prox_circle(P_Tk1[i_T] - self.model.prox_r_T[i_N] * xi_T[i_T], self.model.mu[i_N] * P_Nk1[i_N])
                else:
                    R[self.nR_smooth+self.nla_N+offset:self.nR_smooth+self.nla_N+offset+nT] = P_Tk1[i_T]
                offset += nT
        return R

    def solve(self):
        
        # lists storing output variables
        q = [self.qk]
        u = [self.uk]
        la_g = [self.la_gk]
        la_gamma = [self.la_gammak]
        P_N = [self.P_Nk]
        P_T = [self.P_Tk]

        pbar = tqdm(self.t[:-1])
        for _ in pbar:
            (converged, j, error), tk1, qk1, uk1, la_gk1, la_gammak1, P_Nk1, P_Tk1 = self.step()
            pbar.set_description(f't: {tk1:0.2e}; internal iterations: {j+1}; error: {error:.3e}')
            if not converged:
                raise RuntimeError(f'internal iteration not converged after {j+1} iterations with error: {error:.5e}')

            qk1, uk1 = self.model.step_callback(tk1, qk1, uk1)

            q.append(qk1)
            u.append(uk1)
            la_g.append(la_gk1)
            la_gamma.append(la_gammak1)
            P_N.append(P_Nk1)
            P_T.append(P_Tk1)

            # update local variables for accepted time step
            self.tk, self.qk, self.uk, self.la_gk, self.la_gammak, self.P_Nk, self.P_Tk = tk1, qk1, uk1, la_gk1, la_gammak1, P_Nk1, P_Tk1
            
        # write solution
        return Solution(t=self.t, q=np.array(q), u=np.array(u), la_g=np.array(la_g), la_gamma=np.array(la_gamma), P_N=np.array(P_N), P_T=np.array(P_T))

class Moreau_sym():
    def __init__(self, model, t1, dt, newton_tol=1e-6, newton_max_iter=50, error_function=lambda x: np.max(np.abs(x)) / len(x)):
        self.model = model

        # integration time
        t0 = model.t0
        self.t1 = t1 if t1 > t0 else ValueError("t1 must be larger than initial time t0.")
        self.dt = dt
        self.t = np.arange(t0, self.t1 + self.dt, self.dt)

        self.newton_tol = newton_tol
        self.newton_max_iter = newton_max_iter
        self.newton_error_function = error_function

        self.nq = self.model.nq
        self.nu = self.model.nu
        self.nla_g = self.model.nla_g
        self.nla_gamma = self.model.nla_gamma
        self.nla_N = self.model.nla_N
        self.nla_T = self.model.nla_T
        self.nR_smooth = self.nu + self.nla_g + self.nla_gamma
        self.nR = self.nR_smooth + self.nla_N + self.nla_T

        self.tk = model.t0
        self.qk = model.q0 
        self.uk = model.u0 
        self.la_gk = model.la_g0
        self.la_gammak = model.la_gamma0
        self.la_Nk = model.la_N0
        self.la_Tk = model.la_T0

        # TODO:
        self.NT_connectivity = self.model.NT_connectivity

        self.DOFs_smooth = np.arange(self.nR_smooth)

    def step(self):
        # general quantities
        dt = self.dt
        uk = self.uk
        tk1 = self.tk + dt
        self.qk1 = qk1 = self.qk + dt * self.model.q_dot(self.tk, self.qk, uk)

        # initial residual and error
        R = np.zeros(self.nR)
        uk1 = uk.copy() 
        la_gk1 = self.la_gk.copy()
        la_gammak1 = self.la_gammak.copy()
        la_Nk1 = self.la_Nk.copy()
        la_Tk1 = self.la_Tk.copy()
        # la_Nk1 = np.zeros_like(self.la_Nk)
        # la_Tk1 = np.zeros_like(self.la_Tk)
        xk1 = np.concatenate( (uk1, la_gk1, la_gammak1, la_Nk1, la_Tk1) )

        # identify active normal and tangential contacts
        g_N = self.model.g_N(tk1, qk1)
        self.I_N = I_N = (g_N <= 0)
        if np.any(I_N):
            self.I_T = np.array([c for i, I_N_i in enumerate(I_N) for c in self.model.NT_connectivity[i] if I_N_i], dtype=int)
        else:
            self.I_T = np.array([], dtype=int)

        R = self.__R_newton(tk1, xk1)

        error = self.newton_error_function(R)
        converged = error < self.newton_tol 
        j = 0
        if not converged:
            for j in range(self.newton_max_iter):

                R_x = Numerical_derivative(self.__R_newton, order=2)._x(tk1, xk1)

                # Newton update
                dx = spsolve(R_x, R)
                # try:
                #     dx = spsolve(R_x, R)
                # except:
                #     R = self.__R_newton(tk1, xk1)
                
                xk1 -= dx
                uk1 = xk1[:self.nu]
                la_gk1 = xk1[self.nu:self.nu+self.nla_g]
                la_gammak1 = xk1[self.nu+self.nla_g:self.nu+self.nla_g+self.nla_gamma]
                la_Nk1[I_N] = xk1[self.nR_smooth:self.nR_smooth+np.count_nonzero(I_N)]
                la_Nk1[~I_N] = xk1[self.nR_smooth+np.count_nonzero(I_N):self.nR_smooth+self.nla_N]
                offset = 0
                for i_T in self.NT_connectivity:
                    nT = len(i_T)
                    if nT:
                        la_Tk1[i_T] = xk1[self.nR_smooth+self.nla_N+offset:self.nR_smooth+self.nla_N+offset+nT]
                        offset += nT
                
                R = self.__R_newton(tk1, xk1)
                error = self.newton_error_function(R)
                converged = error < self.newton_tol
                if converged:
                    break
        
        return (converged, j, error), tk1, qk1, uk1, la_gk1, la_gammak1, la_Nk1, la_Tk1

    def __R_newton(self, tk1, xk1):
        dt = self.dt
        uk = self.uk
        qk1 = self.qk1
        I_T = self.I_T
        I_N = self.I_N
        nI_N = np.count_nonzero(I_N)

        uk1 = xk1[:self.nu]
        la_gk1 = xk1[self.nu:self.nu+self.nla_g]
        la_gammak1 = xk1[self.nu+self.nla_g:self.nu+self.nla_g+self.nla_gamma]
        la_Nk1 = np.zeros(self.nla_N)
        la_Nk1[I_N] = xk1[self.nR_smooth:self.nR_smooth+nI_N]
        la_Nk1[~I_N] = xk1[self.nR_smooth+nI_N:self.nR_smooth+self.nla_N]
        la_Tk1 = xk1[self.nR_smooth+self.nla_N:]

        M = self.model.M(tk1, qk1)
        hk = self.model.h(tk1, qk1, uk)
        hk1 = self.model.h(tk1, qk1, uk1)
        W_g = self.model.W_g(tk1, qk1)
        W_gamma = self.model.W_gamma(tk1, qk1)
        W_N = self.model.W_N(tk1, qk1, scipy_matrix=csc_matrix)
        W_T = self.model.W_T(tk1, qk1, scipy_matrix=csc_matrix)
        xi_N = self.model.xi_N(tk1, qk1, uk, uk1)
        xi_T = self.model.xi_T(tk1, qk1, uk, uk1)

        R = np.zeros(self.nR)
        R[:self.nu] = M @ (uk1 - uk) - dt * (0.5 * (hk + hk1) + W_g @ la_gk1 + W_gamma @ la_gammak1) - W_N[:, I_N] @ la_Nk1[I_N] - W_T[:, I_T] @ la_Tk1[I_T]
        R[self.nu:self.nu+self.nla_g] = self.model.g_dot(tk1, qk1, uk1)
        R[self.nu+self.nla_g:self.nu+self.nla_g+self.nla_gamma] = self.model.gamma(tk1, qk1, uk1)
        R[self.nR_smooth:self.nR_smooth+nI_N] = la_Nk1[I_N] - prox_Rn0(la_Nk1[I_N] - self.model.prox_r_N[I_N] * xi_N[I_N])
        R[self.nR_smooth+nI_N:self.nR_smooth+self.nla_N] = la_Nk1[~I_N]

        offset = 0
        for i_N, i_T in enumerate(self.NT_connectivity):
            nT = len(i_T)
            if nT:
                if self.I_N[i_N]:
                    R[self.nR_smooth+self.nla_N+offset:self.nR_smooth+self.nla_N+offset+nT] = la_Tk1[i_T] - prox_circle(la_Tk1[i_T] - self.model.prox_r_T[i_N] * xi_T[i_T], self.model.mu[i_N] * la_Nk1[i_N])
                else:
                    R[self.nR_smooth+self.nla_N+offset:self.nR_smooth+self.nla_N+offset+nT] = la_Tk1[i_T]
                offset += nT
        return R

    def solve(self):
        
        # lists storing output variables
        q = [self.qk]
        u = [self.uk]
        la_g = [self.la_gk]
        la_gamma = [self.la_gammak]
        la_N = [self.la_Nk]
        la_T = [self.la_Tk]

        pbar = tqdm(self.t[:-1])
        for _ in pbar:
            (converged, j, error), tk1, qk1, uk1, la_gk1, la_gammak1, la_Nk1, la_Tk1 = self.step()
            pbar.set_description(f't: {tk1:0.2e}; internal iterations: {j+1}; error: {error:.3e}')
            if not converged:
                raise RuntimeError(f'internal iteration not converged after {j+1} iterations with error: {error:.5e}')

            qk1, uk1 = self.model.step_callback(tk1, qk1, uk1)

            q.append(qk1)
            u.append(uk1)
            la_g.append(la_gk1)
            la_gamma.append(la_gammak1)
            la_N.append(la_Nk1)
            la_T.append(la_Tk1)

            # update local variables for accepted time step
            self.tk, self.qk, self.uk, self.la_gk, self.la_gammak, self.la_Nk, self.la_Tk = tk1, qk1, uk1, la_gk1, la_gammak1, la_Nk1, la_Tk1
            
        # write solution
        return Solution(t=self.t, q=np.array(q), u=np.array(u), la_g=np.array(la_g), la_gamma=np.array(la_gamma), la_N=np.array(la_N), la_T=np.array(la_T))