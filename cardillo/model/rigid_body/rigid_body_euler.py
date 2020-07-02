import numpy as np
from cardillo.math.algebra import cross3, ax2skew, A_IK_basic_x, A_IK_basic_y, A_IK_basic_z, dA_IK_basic_x, dA_IK_basic_y, dA_IK_basic_z, inverse3D

from cardillo.math.numerical_derivative import Numerical_derivative

class Rigid_body_euler():
    def __init__(self, m, K_theta_S, axis='zxy', q0=None, u0=None):
        self.m = m
        self.theta = K_theta_S

        self.nq = 6
        self.nu = 6
        self.q0 = np.zeros(self.nq) if q0 is None else q0
        self.u0 = np.zeros(self.nu) if u0 is None else u0

        self.M_ = np.zeros((self.nu, self.nu))
        self.M_[:3, :3] = m * np.eye(3)
        self.M_[3:, 3:] = self.theta

        
        ex, ey, ez = np.eye(3)
        axis = axis.lower()
        self.e1 = eval(f'e{axis[0]}') 
        self.e2 = eval(f'e{axis[1]}') 
        self.e3 = eval(f'e{axis[2]}') 

        self.A_I1 = eval(f'lambda t,q: A_IK_basic_{axis[0]}(q[3])')
        self.A_12 = eval(f'lambda t,q: A_IK_basic_{axis[1]}(q[4])')
        self.A_2K = eval(f'lambda t,q: A_IK_basic_{axis[2]}(q[5])')

        self.dA_I1 = eval(f'lambda t,q: dA_IK_basic_{axis[0]}(q[3])')
        self.dA_12 = eval(f'lambda t,q: dA_IK_basic_{axis[1]}(q[4])')
        self.dA_2K = eval(f'lambda t,q: dA_IK_basic_{axis[2]}(q[5])')

    def M(self, t, q, M_coo):
        M_coo.extend(self.M_, (self.uDOF, self.uDOF))

    def f_gyr(self, t, q, u):
        omega = u[3:]
        f = np.zeros(self.nu)
        f[3:] = cross3(omega, self.theta @ omega)
        return f

    def f_gyr_u(self, t, q, u, coo):
        omega = u[3:]
        dense = np.zeros((self.nu, self.nu))
        dense[3:, 3:] = ax2skew(omega) @ self.theta - ax2skew(self.theta @ omega)
        coo.extend(dense, (self.uDOF, self.uDOF))

    def q_dot(self, t, q, u):
        q_dot = np.zeros(self.nq)
        q_dot[:3] = u[:3]
        q_dot[3:] = self.Q(t, q) @ u[3:]

        return q_dot
    
    def Q(self, t, q):
        A_K2 = self.A_2K(t, q).T
        A_K1 = A_K2 @ self.A_12(t, q).T
        H_ = np.zeros((3, 3))
        H_[:, 0] = A_K1 @ self.e1
        H_[:, 1] = A_K2 @ self.e2
        H_[:, 2] = self.e3
        return inverse3D(H_) 

    def q_dot_q(self, t, q, u, coo):
        dense = Numerical_derivative(self.q_dot, order=2)._x(t, q, u)
        coo.extend(dense, (self.qDOF, self.qDOF))

    def B_dense(self, t, q):
        B = np.zeros((self.nq, self.nu))
        B[:3, :3] = np.eye(3)
        B[3:, 3:] = self.Q(t, q)
        return B

    def B(self, t, q, coo):
        coo.extend(self.B_dense(t, q), (self.qDOF, self.uDOF))

    def qDOF_P(self, frame_ID=None):
        return self.qDOF

    def uDOF_P(self, frame_ID=None):
        return self.uDOF

    def A_IK(self, t, q, frame_ID=None):
        return self.A_I1(t, q) @ self.A_12(t, q) @ self.A_2K(t, q)

    def A_IK_q(self, t, q, frame_ID=None):
        A_IK_q = np.zeros((3, 3, self.nq))
        A_IK_q[:, :, 3] = self.dA_I1(t, q) @ self.A_12(t, q) @ self.A_2K(t, q)
        A_IK_q[:, :, 4] = self.A_I1(t, q) @ self.dA_12(t, q) @ self.A_2K(t, q)
        A_IK_q[:, :, 5] = self.A_I1(t, q) @ self.A_12(t, q) @ self.dA_2K(t, q)
        return A_IK_q

    def r_OP(self, t, q, frame_ID=None, K_r_SP=np.zeros(3)):
        return q[:3] + self.A_IK(t, q) @ K_r_SP

    def r_OP_q(self, t, q, frame_ID=None, K_r_SP=np.zeros(3)):
        r_OP_q = np.zeros((3, self.nq))
        r_OP_q[:, :3] = np.eye(3)
        r_OP_q[:, :] += np.einsum('ijk,j->ik', self.A_IK_q(t, q), K_r_SP)
        return r_OP_q

    def v_P(self, t, q, u, frame_ID=None, K_r_SP=np.zeros(3)):
        return u[:3] + self.A_IK(t, q) @ cross3(u[3:], K_r_SP)

    # def v_P_q(self, t, q, u, frame_ID=None, K_r_SP=np.zeros(3)):
    #     return np.einsum('ijk,j->ik', self.A_IK_q(t, q), cross3(u[3:], K_r_SP))

    def J_P(self, t, q, frame_ID=None, K_r_SP=np.zeros(3)):
        J_P = np.zeros((3, self.nu))
        J_P[:, :3] = np.eye(3)
        J_P[:, 3:] = - self.A_IK(t, q) @ ax2skew(K_r_SP)
        return J_P

    def J_P_q(self, t, q, frame_ID=None, K_r_SP=np.zeros(3)):
        J_P_q = np.zeros((3, self.nu, self.nq))
        J_P_q[:, 3:, :] = np.einsum('ijk,jl->ilk', self.A_IK_q(t, q), -ax2skew(K_r_SP))
        return J_P_q

    # def K_Omega(self, t, q, u, frame_ID=None):
    #     return u[3:]

    # def K_Omega_q(self, t, q, u, frame_ID=None):
    #     return np.zeros((3, self.nq))

    def K_J_R(self, t, q, frame_ID=None):
        J_R = np.zeros((3, self.nu))
        J_R[:, 3:] = np.eye(3)
        return J_R

    def K_J_R_q(self, t, q, frame_ID=None):
        return np.zeros((3, self.nu, self.nq))




